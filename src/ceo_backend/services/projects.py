"""Projects and their knowledge: versioned facts, decisions, and open questions.

Every fact write runs against an expected project version, keeps the previous value
in fact_versions, and records where the value came from."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ceo_backend.db import utcnow
from ceo_backend.errors import Conflict, Invalid, NotFound
from ceo_backend.fields import PROJECT_FIELDS, UNKNOWN, missing_fields
from ceo_backend.models import (
    Assessment,
    Decision,
    FactVersion,
    OpenQuestion,
    Project,
    ProjectFact,
)

FACT_STATUSES = ("confirmed", "proposed", "assumed", "disputed")
MAX_VALUE_LENGTH = 4000


@dataclass
class FactChange:
    field: str
    value: str
    unit: str | None = None
    status: str = "confirmed"
    effective_date: str | None = None


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def lock_project(session: Session, project_id: uuid.UUID) -> Project:
    # populate_existing: a cached Project in this session may be stale; the version
    # check must see the committed row.
    project = session.scalar(
        select(Project).where(Project.id == project_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if project is None:
        raise NotFound("Project not found.")
    return project


def current_facts(session: Session, project_id: uuid.UUID) -> dict[str, ProjectFact]:
    rows = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id))
    return {row.field: row for row in rows}


def apply_fact_changes(
    session: Session,
    project_id: uuid.UUID,
    changes: list[FactChange],
    *,
    expected_version: int | None,
    source_kind: str,
    source_message_id: int | None = None,
    source_section_id: int | None = None,
    actor_id: uuid.UUID | None = None,
) -> int:
    """Apply changes atomically. Raises Conflict if the project moved past
    expected_version. Returns the project version after the write. Caller commits."""
    project = lock_project(session, project_id)
    if expected_version is not None and project.version != expected_version:
        raise Conflict(
            "Project facts changed since they were loaded.",
            current_version=project.version,
            expected_version=expected_version,
        )

    for change in changes:
        if change.field not in PROJECT_FIELDS:
            raise Invalid(f"Unknown project field {change.field!r}.")
        if change.status not in FACT_STATUSES:
            raise Invalid(f"Invalid fact status {change.status!r}.")
        change.value = str(change.value).strip()
        if not change.value:
            raise Invalid(f"Empty value for {change.field}.")
        if len(change.value) > MAX_VALUE_LENGTH:
            raise Invalid(f"Value for {change.field} is too long.")
        if change.value == UNKNOWN and not PROJECT_FIELDS[change.field].unknown_allowed:
            raise Invalid(f"{change.field} cannot be marked unknown.")

    existing = current_facts(session, project_id)
    new_version = project.version + 1
    changed = False
    for change in changes:
        current = existing.get(change.field)
        if (
            current is not None
            and current.value == change.value
            and current.unit == change.unit
            and current.status == change.status
        ):
            continue
        session.add(
            FactVersion(
                project_id=project_id,
                field=change.field,
                previous_value=current.value if current else None,
                value=change.value,
                unit=change.unit,
                status=change.status,
                source_message_id=source_message_id,
                source_section_id=source_section_id,
                source_kind=source_kind,
                actor_id=actor_id,
                project_version=new_version,
            )
        )
        if current is None:
            current = ProjectFact(project_id=project_id, field=change.field)
            session.add(current)
            existing[change.field] = current
        current.value = change.value
        current.unit = change.unit
        current.status = change.status
        current.effective_date = change.effective_date
        current.source_message_id = source_message_id
        current.source_section_id = source_section_id
        current.source_kind = source_kind
        current.actor_id = actor_id
        current.project_version = new_version
        if change.field == "name":
            project.name = change.value[:255]
        changed = True

    if changed:
        project.version = new_version
        project.updated_at = utcnow()
    session.flush()
    return project.version


def fact_view(fact: ProjectFact) -> dict:
    source = {"kind": fact.source_kind}
    if fact.source_message_id:
        source["message_id"] = fact.source_message_id
        source["ref"] = f"msg:{fact.source_message_id}"
    if fact.source_section_id:
        source["section_id"] = fact.source_section_id
        source["ref"] = f"doc:{fact.source_section_id}"
    return {
        "value": fact.value,
        "unit": fact.unit,
        "status": fact.status,
        "effective_date": fact.effective_date,
        "source": source,
        "project_version": fact.project_version,
        "updated_at": fact.updated_at.isoformat() if fact.updated_at else None,
    }


def facts_view(session: Session, project_id: uuid.UUID) -> dict[str, dict]:
    facts = current_facts(session, project_id)
    ordered = [name for name in PROJECT_FIELDS if name in facts]
    return {name: fact_view(facts[name]) for name in ordered}


def fact_history(session: Session, project_id: uuid.UUID, field: str | None = None) -> list[dict]:
    query = select(FactVersion).where(FactVersion.project_id == project_id)
    if field:
        query = query.where(FactVersion.field == field)
    rows = session.scalars(query.order_by(FactVersion.id))
    return [
        {
            "field": row.field,
            "previous_value": row.previous_value,
            "value": row.value,
            "unit": row.unit,
            "status": row.status,
            "source_kind": row.source_kind,
            "source_message_id": row.source_message_id,
            "source_section_id": row.source_section_id,
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "project_version": row.project_version,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def missing_project_fields(session: Session, project_id: uuid.UUID) -> list[str]:
    facts = {name: {"value": fact.value} for name, fact in current_facts(session, project_id).items()}
    return missing_fields(facts, PROJECT_FIELDS)


# Decisions


def add_decision(
    session: Session,
    project_id: uuid.UUID,
    description: str,
    rationale: str | None,
    *,
    status: str = "adopted",
    source_kind: str = "chat",
    source_message_id: int | None = None,
    actor_id: uuid.UUID | None = None,
) -> Decision:
    description = description.strip()
    if not description:
        raise Invalid("A decision needs a description.")
    if status not in ("adopted", "proposed"):
        raise Invalid("Decision status must be adopted or proposed.")
    for existing in session.scalars(select(Decision).where(Decision.project_id == project_id)):
        if _normalize(existing.description) == _normalize(description):
            if status == "adopted" and existing.status == "proposed":
                existing.status = "adopted"
                existing.source_message_id = source_message_id or existing.source_message_id
            return existing
    decision = Decision(
        project_id=project_id,
        description=description,
        rationale=(rationale or "").strip() or None,
        status=status,
        source_kind=source_kind,
        source_message_id=source_message_id,
        actor_id=actor_id,
    )
    session.add(decision)
    session.flush()
    return decision


def decision_view(decision: Decision) -> dict:
    return {
        "id": decision.id,
        "description": decision.description,
        "rationale": decision.rationale,
        "status": decision.status,
        "source_kind": decision.source_kind,
        "source_message_id": decision.source_message_id,
        "created_at": decision.created_at.isoformat(),
    }


def list_decisions(session: Session, project_id: uuid.UUID, limit: int | None = None) -> list[dict]:
    query = select(Decision).where(Decision.project_id == project_id).order_by(Decision.id.desc())
    if limit:
        query = query.limit(limit)
    return [decision_view(d) for d in reversed(session.scalars(query).all())]


# Open questions


def add_question(
    session: Session,
    project_id: uuid.UUID,
    description: str,
    *,
    source_kind: str = "chat",
    source_message_id: int | None = None,
) -> OpenQuestion:
    description = description.strip()
    if not description:
        raise Invalid("A question needs a description.")
    query = select(OpenQuestion).where(OpenQuestion.project_id == project_id, OpenQuestion.status == "open")
    for existing in session.scalars(query):
        if _normalize(existing.description) == _normalize(description):
            return existing
    question = OpenQuestion(
        project_id=project_id,
        description=description,
        source_kind=source_kind,
        source_message_id=source_message_id,
    )
    session.add(question)
    session.flush()
    return question


def resolve_question(
    session: Session,
    project_id: uuid.UUID,
    question_id: int,
    resolution: str,
    resolution_message_id: int | None = None,
) -> OpenQuestion:
    question = session.get(OpenQuestion, question_id)
    if question is None or question.project_id != project_id:
        raise NotFound(f"Open question {question_id} not found.")
    if question.status == "resolved":
        return question
    if not resolution.strip():
        raise Invalid("A resolution is required.")
    question.status = "resolved"
    question.resolution = resolution.strip()
    question.resolution_message_id = resolution_message_id
    question.resolved_at = utcnow()
    session.flush()
    return question


def question_view(question: OpenQuestion) -> dict:
    return {
        "id": question.id,
        "description": question.description,
        "status": question.status,
        "resolution": question.resolution,
        "source_kind": question.source_kind,
        "source_message_id": question.source_message_id,
        "resolution_message_id": question.resolution_message_id,
        "created_at": question.created_at.isoformat(),
        "resolved_at": question.resolved_at.isoformat() if question.resolved_at else None,
    }


def list_questions(session: Session, project_id: uuid.UUID, status: str | None = "open") -> list[dict]:
    query = select(OpenQuestion).where(OpenQuestion.project_id == project_id)
    if status:
        query = query.where(OpenQuestion.status == status)
    return [question_view(q) for q in session.scalars(query.order_by(OpenQuestion.id))]


# Projects


def create_draft_project(session: Session, workspace_id: uuid.UUID, user_id: uuid.UUID | None) -> Project:
    project = Project(id=uuid.uuid4(), workspace_id=workspace_id, created_by=user_id, status="draft")
    session.add(project)
    session.flush()
    return project


def latest_assessment(session: Session, project_id: uuid.UUID) -> Assessment | None:
    return session.scalar(
        select(Assessment)
        .where(Assessment.project_id == project_id)
        .order_by(Assessment.created_at.desc())
        .limit(1)
    )


def assessment_view(assessment: Assessment | None, project: Project) -> dict | None:
    if assessment is None:
        return None
    return {
        "id": str(assessment.id),
        "kind": assessment.kind,
        "content": assessment.content,
        "project_version": assessment.project_version,
        "outdated": assessment.project_version != project.version,
        "created_at": assessment.created_at.isoformat(),
    }


def project_summary_row(session: Session, project: Project) -> dict:
    """Compact view for dashboard lists. Missing values stay null, never zero."""
    facts = current_facts(session, project.id)
    brief = latest_assessment(session, project.id)
    content = brief.content if brief else {}

    def value(name: str) -> str | None:
        fact = facts.get(name)
        return fact.value if fact and fact.value != UNKNOWN else None

    open_count = session.scalar(
        select(func.count(OpenQuestion.id)).where(
            OpenQuestion.project_id == project.id, OpenQuestion.status == "open"
        )
    )
    return {
        "id": str(project.id),
        "name": project.name,
        "status": project.status,
        "version": project.version,
        "owner": value("owner"),
        "target_date": value("target_date"),
        "investment": value("investment"),
        "expected_impact": value("financial_impact"),
        "feasibility": content.get("feasibility"),
        "score": content.get("score"),
        "assessment_status": (
            "none" if brief is None else "outdated" if brief.project_version != project.version else "current"
        ),
        "open_questions": open_count,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }
