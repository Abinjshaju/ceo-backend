import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ceo_backend.agents.project import PROMPT_VERSION, build_project_turn
from ceo_backend.api.deps import current_user, get_services, get_session, schedule_jobs, stream_turn
from ceo_backend.api.schemas import ArtifactEventIn, ChatMessageIn, FinalizeIn, ProjectPatch
from ceo_backend.auth import require_membership, require_project
from ceo_backend.errors import Conflict, NotFound
from ceo_backend.models import AgentRun, Document, DocumentVersion, Project, ProjectSummary, ToolCall, User
from ceo_backend.services.artifacts import get_artifact, handle_event, list_artifacts
from ceo_backend.services.briefs import generate_brief
from ceo_backend.services.container import Services
from ceo_backend.services.conversations import (
    get_or_create_conversation,
    message_view,
    project_conversation_ids,
    recent_messages,
)
from ceo_backend.services.documents import document_view, get_document, list_documents
from ceo_backend.services.intake import finalize_project
from ceo_backend.services.projects import (
    FactChange,
    apply_fact_changes,
    assessment_view,
    facts_view,
    fact_history,
    latest_assessment,
    list_decisions,
    list_questions,
    lock_project,
    missing_project_fields,
    project_summary_row,
)
from ceo_backend.services.turns import TurnSpec, run_turn

router = APIRouter()


def project_detail(session: Session, project: Project) -> dict:
    summary = session.get(ProjectSummary, project.id)
    return {
        **project_summary_row(session, project),
        "workspace_id": str(project.workspace_id),
        "facts": facts_view(session, project.id),
        "missing_fields": missing_project_fields(session, project.id),
        "decisions": list_decisions(session, project.id),
        "open_questions": list_questions(session, project.id),
        "summary": summary.summary if summary else None,
        "latest_assessment": assessment_view(latest_assessment(session, project.id), project),
        "documents": [document_view(session, d) for d in list_documents(session, project.workspace_id, project.id, True)],
    }


@router.get("/workspaces/{workspace_id}/projects")
def list_projects(workspace_id: uuid.UUID, search: str | None = None,
                  status: str | None = Query(default=None, pattern="^(draft|active|archived)$"),
                  limit: int = Query(default=25, ge=1, le=100), offset: int = Query(default=0, ge=0),
                  user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    require_membership(session, user, workspace_id)
    query = select(Project).where(Project.workspace_id == workspace_id)
    if status:
        query = query.where(Project.status == status)
    else:
        query = query.where(Project.status != "archived")
    if search:
        escaped = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(or_(Project.name.ilike(f"%{escaped}%", escape="\\")))
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    projects = session.scalars(query.order_by(Project.updated_at.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "limit": limit, "offset": offset,
            "projects": [project_summary_row(session, p) for p in projects]}


@router.get("/projects/{project_id}")
def get_project(project_id: uuid.UUID, user: User = Depends(current_user),
                session: Session = Depends(get_session)) -> dict:
    return project_detail(session, require_project(session, user, project_id))


@router.patch("/projects/{project_id}")
def patch_project(project_id: uuid.UUID, body: ProjectPatch, user: User = Depends(current_user),
                  session: Session = Depends(get_session)) -> dict:
    project = require_project(session, user, project_id)
    if body.changes:
        apply_fact_changes(
            session, project.id,
            [FactChange(field=c.field, value=c.value, unit=c.unit, status=c.status, effective_date=c.effective_date)
             for c in body.changes],
            expected_version=body.expected_version, source_kind="form", actor_id=user.id,
        )
    if body.status:
        locked = lock_project(session, project.id)
        if locked.status == "draft":
            raise Conflict("Finalize the project before changing its status.")
        locked.status = body.status
    session.commit()
    return project_detail(session, session.get(Project, project.id, populate_existing=True))


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: uuid.UUID, user: User = Depends(current_user), session: Session = Depends(get_session),
                   services: Services = Depends(get_services)) -> Response:
    project = require_project(session, user, project_id)
    keys = session.scalars(
        select(DocumentVersion.storage_key).join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.project_id == project.id)
    ).all()
    session.delete(project)
    session.commit()
    for key in keys:
        services.storage.delete(key)
    return Response(status_code=204)


@router.post("/projects/{project_id}/finalize")
def finalize(project_id: uuid.UUID, body: FinalizeIn, background: BackgroundTasks,
             user: User = Depends(current_user), session: Session = Depends(get_session),
             services: Services = Depends(get_services)) -> dict:
    project = require_project(session, user, project_id)
    result = finalize_project(session, project.id, body.idempotency_key)
    schedule_jobs(background, services)
    return result


@router.get("/projects/{project_id}/messages")
def get_messages(project_id: uuid.UUID, before: int | None = None,
                 limit: int = Query(default=50, ge=1, le=200), include_onboarding: bool = False,
                 user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    project = require_project(session, user, project_id)
    if include_onboarding:
        conversation_ids = project_conversation_ids(session, project.id)
    else:
        conversation_ids = [get_or_create_conversation(session, project.workspace_id, "project_chat", project.id).id]
        session.commit()
    rows = recent_messages(session, conversation_ids, limit=limit, before_id=before)
    return {"messages": [message_view(m) for m in rows], "has_more": len(rows) == limit}


def _project_turn(services: Services, session: Session, user_id: uuid.UUID, project_id: uuid.UUID,
                  body: ChatMessageIn, emit=None, stream: bool = False) -> dict:
    user = session.get(User, user_id)
    project = require_project(session, user, project_id)
    if project.status != "active":
        raise Conflict("Finish the project intake before chatting about it.")
    document_ids = []
    for document_id in body.document_ids:
        get_document(session, project.workspace_id, project.id, document_id)
        document_ids.append(str(document_id))
    conversation = get_or_create_conversation(session, project.workspace_id, "project_chat", project.id)
    session.commit()
    conversation_ids = project_conversation_ids(session, project.id)
    spec = TurnSpec(
        kind="project_chat",
        prompt_version=PROMPT_VERSION,
        conversation=conversation,
        build=lambda state, message: build_project_turn(
            state, state.session.get(Project, project.id), message, conversation_ids),
        extra=lambda s: {"context": {"project_version": s.get(Project, project.id, populate_existing=True).version}},
    )
    return run_turn(services, session, user, spec, body.client_message_id, body.content, document_ids,
                    emit=emit, stream=stream)


@router.post("/projects/{project_id}/messages")
def post_message(project_id: uuid.UUID, body: ChatMessageIn, background: BackgroundTasks, stream: bool = False,
                 user: User = Depends(current_user), session: Session = Depends(get_session),
                 services: Services = Depends(get_services)):
    require_project(session, user, project_id)
    if stream:
        return stream_turn(services, lambda s, emit: _project_turn(services, s, user.id, project_id, body, emit, True))
    result = _project_turn(services, session, user.id, project_id, body)
    schedule_jobs(background, services)
    return result


@router.get("/projects/{project_id}/facts")
def get_facts(project_id: uuid.UUID, history: bool = False, field: str | None = None,
              user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    project = require_project(session, user, project_id)
    result = {"project_version": project.version, "facts": facts_view(session, project.id)}
    if history:
        result["history"] = fact_history(session, project.id, field)
    return result


@router.get("/projects/{project_id}/decisions")
def get_decisions(project_id: uuid.UUID, user: User = Depends(current_user),
                  session: Session = Depends(get_session)) -> dict:
    project = require_project(session, user, project_id)
    return {"decisions": list_decisions(session, project.id)}


@router.get("/projects/{project_id}/open-questions")
def get_questions(project_id: uuid.UUID, status: str | None = Query(default="open", pattern="^(open|resolved|all)$"),
                  user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    project = require_project(session, user, project_id)
    return {"open_questions": list_questions(session, project.id, None if status == "all" else status)}


@router.post("/projects/{project_id}/assessments")
def create_assessment(project_id: uuid.UUID, user: User = Depends(current_user),
                      session: Session = Depends(get_session), services: Services = Depends(get_services)) -> dict:
    project = require_project(session, user, project_id)
    if project.status != "active":
        raise Conflict("Finalize the project before generating a brief.")
    assessment = generate_brief(services, session, project, actor_id=user.id)
    session.commit()
    return assessment_view(assessment, project)


@router.get("/projects/{project_id}/assessments/latest")
def get_latest_assessment(project_id: uuid.UUID, user: User = Depends(current_user),
                          session: Session = Depends(get_session)) -> dict:
    project = require_project(session, user, project_id)
    view = assessment_view(latest_assessment(session, project.id), project)
    if view is None:
        raise NotFound("No decision brief yet.")
    return view


@router.get("/projects/{project_id}/artifacts")
def get_artifacts(project_id: uuid.UUID, status: str | None = None, user: User = Depends(current_user),
                  session: Session = Depends(get_session)) -> dict:
    project = require_project(session, user, project_id)
    return {"artifacts": list_artifacts(session, project, status)}


@router.post("/projects/{project_id}/artifacts/{artifact_id}/events")
def artifact_event(project_id: uuid.UUID, artifact_id: uuid.UUID, body: ArtifactEventIn,
                   user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    project = require_project(session, user, project_id)
    artifact = get_artifact(session, project, artifact_id)
    return handle_event(
        session, project, artifact, user.id, body.operation, body.inputs,
        [c.model_dump() for c in body.changes] if body.changes else None, body.expected_version, body.name,
    )


@router.get("/agent-runs/{run_id}")
def get_run(run_id: uuid.UUID, user: User = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    run = session.get(AgentRun, run_id)
    if run is None:
        raise NotFound("Run not found.")
    require_membership(session, user, run.workspace_id)
    calls = session.scalars(select(ToolCall).where(ToolCall.run_id == run.id).order_by(ToolCall.id)).all()
    return {
        "id": str(run.id),
        "kind": run.kind,
        "status": run.status,
        "error": run.error,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "user_message_id": run.user_message_id,
        "assistant_message_id": run.assistant_message_id,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "created_at": run.created_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "tool_calls": [{"name": c.name, "ok": c.ok, "duration_ms": c.duration_ms} for c in calls],
    }
