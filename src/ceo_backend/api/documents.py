"""Document routes for project documents and company-level shared documents, plus
the signed-URL endpoints used by local storage."""

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ceo_backend.api.deps import current_user, get_services, get_session, schedule_jobs
from ceo_backend.api.schemas import CompleteUploadIn, UploadUrlIn
from ceo_backend.auth import require_membership, require_project
from ceo_backend.errors import Invalid, NotFound
from ceo_backend.models import User
from ceo_backend.services import documents as docs
from ceo_backend.services.container import Services
from ceo_backend.services.storage import LocalStorage

router = APIRouter()


def _scope(session: Session, user: User, project_id: uuid.UUID | None, workspace_id: uuid.UUID | None):
    if project_id is not None:
        project = require_project(session, user, project_id)
        return project.workspace_id, project.id
    require_membership(session, user, workspace_id)
    return workspace_id, None


def _register(prefix: str, scope_param: str) -> None:
    """Register the document routes under /projects/{project_id} or /workspaces/{workspace_id}."""

    def resolve(session: Session, user: User, scope_id: uuid.UUID):
        if scope_param == "project_id":
            return _scope(session, user, scope_id, None)
        return _scope(session, user, None, scope_id)

    @router.post(f"{prefix}/documents/upload-url", name=f"{scope_param}_upload_url")
    def upload_url(scope_id: uuid.UUID, body: UploadUrlIn, user: User = Depends(current_user),
                   session: Session = Depends(get_session), services: Services = Depends(get_services)) -> dict:
        workspace_id, project_id = resolve(session, user, scope_id)
        return docs.request_upload(
            services, session, workspace_id=workspace_id, project_id=project_id, user_id=user.id,
            filename=body.filename, content_type=body.content_type, size_bytes=body.size_bytes,
            document_id=body.document_id,
        )

    @router.post(f"{prefix}/documents/{{document_id}}/complete", name=f"{scope_param}_complete")
    def complete(scope_id: uuid.UUID, document_id: uuid.UUID, body: CompleteUploadIn, background: BackgroundTasks,
                 user: User = Depends(current_user), session: Session = Depends(get_session),
                 services: Services = Depends(get_services)) -> dict:
        workspace_id, project_id = resolve(session, user, scope_id)
        document = docs.get_document(session, workspace_id, project_id, document_id)
        docs.complete_upload(services, session, document, body.version_id)
        schedule_jobs(background, services)
        return docs.document_view(session, document)

    @router.get(f"{prefix}/documents", name=f"{scope_param}_list")
    def list_all(scope_id: uuid.UUID, user: User = Depends(current_user),
                 session: Session = Depends(get_session)) -> dict:
        workspace_id, project_id = resolve(session, user, scope_id)
        return {"documents": [docs.document_view(session, d) for d in docs.list_documents(session, workspace_id, project_id)]}

    @router.get(f"{prefix}/documents/{{document_id}}/status", name=f"{scope_param}_status")
    def status(scope_id: uuid.UUID, document_id: uuid.UUID, user: User = Depends(current_user),
               session: Session = Depends(get_session)) -> dict:
        workspace_id, project_id = resolve(session, user, scope_id)
        return docs.document_view(session, docs.get_document(session, workspace_id, project_id, document_id))

    @router.get(f"{prefix}/documents/{{document_id}}/download-url", name=f"{scope_param}_download")
    def download(scope_id: uuid.UUID, document_id: uuid.UUID, user: User = Depends(current_user),
                 session: Session = Depends(get_session), services: Services = Depends(get_services)) -> dict:
        workspace_id, project_id = resolve(session, user, scope_id)
        document = docs.get_document(session, workspace_id, project_id, document_id)
        return {"url": docs.download_url(services, session, document)}

    @router.post(f"{prefix}/documents/{{document_id}}/retry", name=f"{scope_param}_retry")
    def retry(scope_id: uuid.UUID, document_id: uuid.UUID, background: BackgroundTasks,
              user: User = Depends(current_user), session: Session = Depends(get_session),
              services: Services = Depends(get_services)) -> dict:
        workspace_id, project_id = resolve(session, user, scope_id)
        document = docs.get_document(session, workspace_id, project_id, document_id)
        docs.retry(session, document)
        schedule_jobs(background, services)
        return docs.document_view(session, document)

    @router.delete(f"{prefix}/documents/{{document_id}}", name=f"{scope_param}_delete", status_code=204)
    def remove(scope_id: uuid.UUID, document_id: uuid.UUID, user: User = Depends(current_user),
               session: Session = Depends(get_session), services: Services = Depends(get_services)) -> Response:
        workspace_id, project_id = resolve(session, user, scope_id)
        document = docs.get_document(session, workspace_id, project_id, document_id)
        docs.delete_document(services, session, document, user.id)
        return Response(status_code=204)


_register("/projects/{scope_id}", "project_id")
_register("/workspaces/{scope_id}", "workspace_id")


def _local(services: Services) -> LocalStorage:
    if not isinstance(services.storage, LocalStorage):
        raise NotFound("Not found.")
    return services.storage


@router.put("/storage/objects/{token}", status_code=204)
async def put_object(token: str, request: Request, services: Services = Depends(get_services)) -> Response:
    storage = _local(services)
    payload = storage.verify(token, "put")
    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    if content_type and content_type != payload["ct"]:
        raise Invalid("Content-Type does not match the upload request.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > payload["max"]:
            raise Invalid("The file is larger than declared.")
    storage.write(payload["k"], bytes(data))
    return Response(status_code=204)


@router.get("/storage/objects/{token}")
def get_object(token: str, services: Services = Depends(get_services)) -> Response:
    storage = _local(services)
    payload = storage.verify(token, "get")
    filename = payload.get("fn", "download").replace('"', "")
    return Response(
        content=storage.read(payload["k"]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store"},
    )
