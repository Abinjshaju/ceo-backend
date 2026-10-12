"""Onboarding sessions: start or resume intake, and finalize projects idempotently."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ceo_backend.db import utcnow
from ceo_backend.errors import Conflict, Invalid
from ceo_backend.models import Conversation, OnboardingSession, Project, User
from ceo_backend.services.briefs import enqueue_brief
from ceo_backend.services.company import get_profile, profile_view
from ceo_backend.services.conversations import get_or_create_conversation, message_view, recent_messages
from ceo_backend.services.documents import document_view, list_documents
from ceo_backend.services.projects import create_draft_project, facts_view, lock_project, missing_project_fields


def start_session(session: Session, workspace_id: uuid.UUID, user: User, kind: str) -> OnboardingSession:
    """Return the active session of this kind, or start one. Starting is safe to repeat."""
    if kind not in ("company", "project"):
        raise Invalid("kind must be company or project.")
    active = session.scalar(
        select(OnboardingSession)
        .where(OnboardingSession.workspace_id == workspace_id, OnboardingSession.kind == kind,
               OnboardingSession.status == "active")
        .order_by(OnboardingSession.created_at.desc())
        .limit(1)
    )
    if active is not None:
        return active

    profile = get_profile(session, workspace_id)
    if kind == "company" and profile.status == "complete":
        raise Conflict("The company profile is complete. Edit it with PATCH /workspaces/{id}/company.")
    if kind == "project" and profile.status != "complete":
        raise Conflict("Complete the company profile before adding a project.")

    project = create_draft_project(session, workspace_id, user.id) if kind == "project" else None
    conversation = Conversation(
        id=uuid.uuid4(), workspace_id=workspace_id, kind=f"{kind}_onboarding",
        project_id=project.id if project else None,
    )
    session.add(conversation)
    onboarding = OnboardingSession(
        id=uuid.uuid4(), workspace_id=workspace_id, kind=kind, project_id=project.id if project else None,
        conversation_id=conversation.id, status="active", state={}, created_by=user.id,
    )
    session.add(onboarding)
    session.commit()
    return onboarding


def project_intake_state(session: Session, onboarding: OnboardingSession) -> dict:
    project = session.get(Project, onboarding.project_id)
    documents = [document_view(session, d) for d in list_documents(session, project.workspace_id, project.id)]
    return {
        "project_id": str(project.id),
        "project_status": project.status,
        "project_version": project.version,
        "facts": {name: {"value": f["value"], "status": f["status"]} for name, f in facts_view(session, project.id).items()},
        "missing": missing_project_fields(session, project.id),
        "documents_answer": (onboarding.state or {}).get("documents"),
        "documents": [
            {k: d[k] for k in ("id", "filename", "status", "searchable")} for d in documents
        ],
    }


def company_intake_state(session: Session, onboarding: OnboardingSession) -> dict:
    view = profile_view(get_profile(session, onboarding.workspace_id))
    return {k: view[k] for k in ("status", "version", "fields", "missing", "research")}


def intake_state(session: Session, onboarding: OnboardingSession) -> dict:
    if onboarding.kind == "company":
        return company_intake_state(session, onboarding)
    return project_intake_state(session, onboarding)


def session_view(session: Session, onboarding: OnboardingSession, include_messages: bool = True) -> dict:
    view = {
        "id": str(onboarding.id),
        "workspace_id": str(onboarding.workspace_id),
        "kind": onboarding.kind,
        "status": onboarding.status,
        "project_id": str(onboarding.project_id) if onboarding.project_id else None,
        "intake": intake_state(session, onboarding),
        "created_at": onboarding.created_at.isoformat(),
        "completed_at": onboarding.completed_at.isoformat() if onboarding.completed_at else None,
    }
    if include_messages:
        view["messages"] = [message_view(m) for m in recent_messages(session, [onboarding.conversation_id], limit=200)]
    return view


def finalize_project(session: Session, project_id: uuid.UUID, idempotency_key: str | None,
                     require_documents_answer: OnboardingSession | None = None) -> dict:
    """Activate a draft project. Repeating the call returns the same result."""
    project = lock_project(session, project_id)
    if project.status == "active":
        return {"ok": True, "project_id": str(project.id), "status": "active", "already_finalized": True}
    if project.status != "draft":
        raise Conflict(f"A {project.status} project cannot be finalized.")
    missing = missing_project_fields(session, project.id)
    if missing:
        raise Invalid("Required project fields are missing.", missing=missing)
    if require_documents_answer is not None and not (require_documents_answer.state or {}).get("documents"):
        raise Invalid("Ask whether there are supporting documents before finalizing.", missing=["documents"])

    project.status = "active"
    project.finalized_at = utcnow()
    project.finalize_key = idempotency_key
    get_or_create_conversation(session, project.workspace_id, "project_chat", project.id)
    onboarding = session.scalar(
        select(OnboardingSession).where(OnboardingSession.project_id == project.id, OnboardingSession.status == "active")
    )
    if onboarding is not None:
        onboarding.status = "completed"
        onboarding.completed_at = utcnow()
    enqueue_brief(session, project)
    session.commit()
    return {"ok": True, "project_id": str(project.id), "status": "active", "already_finalized": False,
            "decision_brief": "queued"}
