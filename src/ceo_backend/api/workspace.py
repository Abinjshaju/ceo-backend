import uuid

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ceo_backend.agents.onboarding import PROMPT_VERSION, build_onboarding_turn
from ceo_backend.api.deps import current_user, get_services, get_session, schedule_jobs, stream_turn
from ceo_backend.api.schemas import ChatMessageIn, CompanyPatch, OnboardingStart
from ceo_backend.auth import require_membership
from ceo_backend.errors import Conflict, NotFound
from ceo_backend.models import Conversation, OnboardingSession, User, Workspace, WorkspaceMember
from ceo_backend.db import utcnow
from ceo_backend.services.company import complete_profile, get_profile, profile_view, save_company_fields
from ceo_backend.services.container import Services
from ceo_backend.services.documents import get_document
from ceo_backend.services.intake import intake_state, session_view, start_session
from ceo_backend.services.turns import TurnSpec, run_turn

router = APIRouter()


@router.get("/me")
def me(user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    rows = session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at)
    ).all()
    workspaces = []
    for workspace, role in rows:
        active = session.scalars(
            select(OnboardingSession).where(
                OnboardingSession.workspace_id == workspace.id, OnboardingSession.status == "active"
            )
        ).all()
        workspaces.append({
            "id": str(workspace.id),
            "name": workspace.name,
            "role": role,
            "company_status": get_profile(session, workspace.id).status,
            "active_onboarding": [
                {"id": str(o.id), "kind": o.kind, "project_id": str(o.project_id) if o.project_id else None}
                for o in active
            ],
        })
    session.commit()
    return {
        "user": {"id": str(user.id), "email": user.email, "name": user.name},
        "workspaces": workspaces,
    }


@router.get("/workspaces/{workspace_id}/company")
def get_company(workspace_id: uuid.UUID, user: User = Depends(current_user),
                session: Session = Depends(get_session)) -> dict:
    require_membership(session, user, workspace_id)
    view = profile_view(get_profile(session, workspace_id))
    session.commit()
    return view


@router.patch("/workspaces/{workspace_id}/company")
def patch_company(workspace_id: uuid.UUID, body: CompanyPatch, user: User = Depends(current_user),
                  session: Session = Depends(get_session)) -> dict:
    require_membership(session, user, workspace_id)
    profile = save_company_fields(
        session, workspace_id, body.fields, source="form", unknown_fields=body.unknown_fields,
        expected_version=body.expected_version,
    )
    if body.complete and profile.status != "complete":
        profile = complete_profile(session, workspace_id)
        for onboarding in session.scalars(select(OnboardingSession).where(
            OnboardingSession.workspace_id == workspace_id, OnboardingSession.kind == "company",
            OnboardingSession.status == "active",
        )):
            onboarding.status, onboarding.completed_at = "completed", utcnow()
    session.commit()
    return profile_view(profile)


@router.post("/workspaces/{workspace_id}/onboarding-sessions")
def create_onboarding(workspace_id: uuid.UUID, body: OnboardingStart, user: User = Depends(current_user),
                      session: Session = Depends(get_session)) -> dict:
    require_membership(session, user, workspace_id)
    onboarding = start_session(session, workspace_id, user, body.kind)
    return session_view(session, onboarding)


def _onboarding(session: Session, user: User, session_id: uuid.UUID) -> OnboardingSession:
    onboarding = session.get(OnboardingSession, session_id)
    if onboarding is None:
        raise NotFound("Onboarding session not found.")
    require_membership(session, user, onboarding.workspace_id)
    return onboarding


@router.get("/onboarding-sessions/{session_id}")
def get_onboarding(session_id: uuid.UUID, user: User = Depends(current_user),
                   session: Session = Depends(get_session)) -> dict:
    return session_view(session, _onboarding(session, user, session_id))


def _onboarding_turn(services: Services, session: Session, user_id: uuid.UUID, session_id: uuid.UUID,
                     body: ChatMessageIn, emit=None, stream: bool = False) -> dict:
    user = session.get(User, user_id)
    onboarding = _onboarding(session, user, session_id)
    if onboarding.status != "active":
        raise Conflict("This onboarding session is already complete.")
    document_ids = []
    if body.document_ids:
        for document_id in body.document_ids:
            get_document(session, onboarding.workspace_id, onboarding.project_id, document_id)
            document_ids.append(str(document_id))
    spec = TurnSpec(
        kind=f"{onboarding.kind}_onboarding",
        prompt_version=PROMPT_VERSION,
        conversation=session.get(Conversation, onboarding.conversation_id),
        build=lambda state, message: build_onboarding_turn(state, onboarding, message),
        extra=lambda s: {"onboarding": {
            "id": str(onboarding.id), "kind": onboarding.kind,
            "status": s.get(OnboardingSession, onboarding.id, populate_existing=True).status,
            "intake": intake_state(s, onboarding),
        }},
    )
    return run_turn(services, session, user, spec, body.client_message_id, body.content, document_ids,
                    emit=emit, stream=stream)


@router.post("/onboarding-sessions/{session_id}/messages")
def post_onboarding_message(session_id: uuid.UUID, body: ChatMessageIn, background: BackgroundTasks,
                            stream: bool = False, user: User = Depends(current_user),
                            session: Session = Depends(get_session),
                            services: Services = Depends(get_services)):
    _onboarding(session, user, session_id)
    if stream:
        return stream_turn(services, lambda s, emit: _onboarding_turn(services, s, user.id, session_id, body, emit, True))
    result = _onboarding_turn(services, session, user.id, session_id, body)
    schedule_jobs(background, services)
    return result
