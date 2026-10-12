"""Document lifecycle: signed upload, completion, versions, retry, download, delete."""

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ceo_backend.db import utcnow
from ceo_backend.errors import Conflict, Invalid, NotFound
from ceo_backend.models import Document, DocumentSection, DocumentVersion, FactVersion, ProjectFact
from ceo_backend.services import jobs
from ceo_backend.services.extraction import SUPPORTED_TYPES, file_kind
from ceo_backend.services.projects import add_question, lock_project


def _clean_filename(filename: str) -> str:
    name = filename.replace("\\", "/").split("/")[-1].strip()
    name = "".join(ch for ch in name if ch.isprintable())
    if not name:
        raise Invalid("A filename is required.")
    return name[:255]


def get_document(session: Session, workspace_id: uuid.UUID, project_id: uuid.UUID | None,
                 document_id: uuid.UUID) -> Document:
    """A document in exactly this scope: a project's own documents, or company-level
    documents when project_id is None."""
    document = session.get(Document, document_id)
    if (
        document is None
        or document.workspace_id != workspace_id
        or document.project_id != project_id
        or document.status == "deleted"
    ):
        raise NotFound("Document not found.")
    return document


def latest_version(session: Session, document_id: uuid.UUID) -> DocumentVersion | None:
    return session.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version.desc())
        .limit(1)
    )


def request_upload(services, session: Session, *, workspace_id: uuid.UUID, project_id: uuid.UUID | None,
                   user_id: uuid.UUID | None, filename: str, content_type: str, size_bytes: int,
                   document_id: uuid.UUID | None = None) -> dict:
    settings = services.settings
    filename = _clean_filename(filename)
    kind = file_kind(filename)
    if kind is None:
        allowed = ", ".join(sorted(SUPPORTED_TYPES))
        raise Invalid(f"Unsupported file type. Supported: {allowed}.")
    if size_bytes <= 0:
        raise Invalid("The file is empty.")
    if size_bytes > settings.max_upload_bytes:
        raise Invalid(f"Files are limited to {settings.max_upload_bytes // (1024 * 1024)} MB.")
    content_type = SUPPORTED_TYPES[kind]

    if document_id:
        document = get_document(session, workspace_id, project_id, document_id)
        if document.status in ("uploading", "processing") and document.current_version_id is None:
            raise Conflict("The first version of this document is still being uploaded or processed.")
        number = (session.scalar(
            select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == document.id)
        ) or 0) + 1
    else:
        document = Document(
            id=uuid.uuid4(), workspace_id=workspace_id, project_id=project_id, filename=filename,
            content_type=content_type, status="uploading", created_by=user_id,
        )
        session.add(document)
        session.flush()
        number = 1

    version = DocumentVersion(
        id=uuid.uuid4(), document_id=document.id, version=number, filename=filename,
        content_type=content_type, declared_size=size_bytes, status="uploading",
        storage_key=f"{workspace_id}/{document.id}/{uuid.uuid4().hex}{kind}",
    )
    session.add(version)
    session.commit()
    target = services.storage.upload_target(version.storage_key, content_type, size_bytes)
    return {"document_id": str(document.id), "version_id": str(version.id), "version": number, "upload": target}


def complete_upload(services, session: Session, document: Document, version_id: uuid.UUID) -> None:
    version = session.get(DocumentVersion, version_id)
    if version is None or version.document_id != document.id:
        raise NotFound("Document version not found.")
    if version.status != "uploading":
        return  # already completed; repeat calls are safe
    size = services.storage.size(version.storage_key)
    if size is None:
        raise Conflict("The file has not been uploaded yet.")
    if size > services.settings.max_upload_bytes or size > version.declared_size:
        services.storage.delete(version.storage_key)
        version.status, version.error = "failed", "The uploaded file is larger than declared."
        if document.current_version_id is None:
            document.status = "failed"
        session.commit()
        raise Invalid(version.error)
    version.size_bytes = size
    version.status = "processing"
    if document.current_version_id is None:
        document.status = "processing"
    jobs.enqueue(session, "ingest_document", {"version_id": str(version.id)}, f"ingest:{version.id}")
    session.commit()


def retry(session: Session, document: Document) -> None:
    version = latest_version(session, document.id)
    if version is None or version.status != "failed":
        raise Conflict("Only failed documents can be retried.")
    if version.size_bytes is None:
        raise Conflict("The upload never completed. Upload the file again.")
    version.status, version.error = "processing", None
    version.extraction_status = version.index_status = "pending"
    if document.current_version_id is None:
        document.status = "processing"
    if not jobs.requeue(session, f"ingest:{version.id}"):
        jobs.enqueue(session, "ingest_document", {"version_id": str(version.id)}, f"ingest:{version.id}")
    session.commit()


def download_url(services, session: Session, document: Document) -> str:
    if document.current_version_id is None:
        raise Conflict("The document has no completed version yet.")
    version = session.get(DocumentVersion, document.current_version_id)
    return services.storage.download_url(version.storage_key, version.filename)


def delete_document(services, session: Session, document: Document, actor_id: uuid.UUID | None) -> None:
    """Remove the originals and searchable content, then review facts that relied on them."""
    section_ids = list(session.scalars(select(DocumentSection.id).where(DocumentSection.document_id == document.id)))
    if section_ids and document.project_id:
        dependent = session.scalars(
            select(ProjectFact).where(
                ProjectFact.project_id == document.project_id,
                ProjectFact.source_section_id.in_(section_ids),
            )
        ).all()
        if dependent:
            project = lock_project(session, document.project_id)
            project.version += 1
            for fact in dependent:
                if fact.status == "confirmed":
                    add_question(
                        session, document.project_id,
                        f'Source document "{document.filename}" was deleted. Confirm {fact.field}: {fact.value}',
                        source_kind="document",
                    )
                    fact.source_section_id = None
                else:
                    session.add(FactVersion(
                        project_id=document.project_id, field=fact.field, previous_value=fact.value,
                        value=fact.value, unit=fact.unit, status="retired", source_kind="document",
                        actor_id=actor_id, project_version=project.version,
                    ))
                    session.delete(fact)

    for version in session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document.id)):
        services.storage.delete(version.storage_key)
    session.execute(delete(DocumentSection).where(DocumentSection.document_id == document.id))
    document.status = "deleted"
    document.deleted_at = utcnow()
    document.current_version_id = None
    session.commit()


def document_view(session: Session, document: Document) -> dict:
    latest = latest_version(session, document.id)
    current = session.get(DocumentVersion, document.current_version_id) if document.current_version_id else None
    return {
        "id": str(document.id),
        "filename": current.filename if current else document.filename,
        "content_type": document.content_type,
        "scope": "project" if document.project_id else "company",
        "project_id": str(document.project_id) if document.project_id else None,
        "status": document.status,
        "searchable": document.status == "ready" and current is not None,
        "current_version": current.version if current else None,
        "page_count": current.page_count if current else None,
        "latest_version": None if latest is None else {
            "id": str(latest.id),
            "version": latest.version,
            "status": latest.status,
            "extraction_status": latest.extraction_status,
            "index_status": latest.index_status,
            "error": latest.error,
            "size_bytes": latest.size_bytes,
        },
        "created_at": document.created_at.isoformat(),
    }


def list_documents(session: Session, workspace_id: uuid.UUID, project_id: uuid.UUID | None,
                   include_company: bool = False) -> list[Document]:
    query = select(Document).where(Document.workspace_id == workspace_id, Document.status != "deleted")
    if project_id and include_company:
        query = query.where((Document.project_id == project_id) | Document.project_id.is_(None))
    else:
        query = query.where(Document.project_id == project_id if project_id else Document.project_id.is_(None))
    return list(session.scalars(query.order_by(Document.created_at)))
