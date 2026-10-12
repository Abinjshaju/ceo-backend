"""Artifacts and their interactive events.

explore       recalculates with new inputs; project facts never change
save_scenario keeps the scenario for later comparison
adopt         applies explicit values to project facts, with a version check
submit_form   saves a form component's answers as project facts"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from ceo_backend.errors import Invalid, NotFound
from ceo_backend.models import Artifact, ArtifactEvent, Message, Project
from ceo_backend.services.projects import FactChange, add_decision, apply_fact_changes
from ceo_backend.services.scenarios import calculate


def artifact_view(artifact: Artifact, project: Project) -> dict:
    session = object_session(artifact)
    message = session.get(Message, artifact.message_id) if session is not None and artifact.message_id else None
    citations = [item for item in (message.citations or []) if item["ref"] in (artifact.source_refs or [])] if message else []
    return {
        "citations": citations,
        "artifact_id": str(artifact.id),
        "type": artifact.type,
        "title": artifact.title,
        "data": artifact.data,
        "inputs": artifact.inputs,
        "source_refs": artifact.source_refs or [],
        "status": artifact.status,
        "saved_name": artifact.saved_name,
        "message_id": artifact.message_id,
        "project_version": artifact.project_version,
        "outdated": artifact.project_version != project.version,
        "created_at": artifact.created_at.isoformat(),
    }


def get_artifact(session: Session, project: Project, artifact_id: uuid.UUID) -> Artifact:
    artifact = session.get(Artifact, artifact_id)
    if artifact is None or artifact.project_id != project.id:
        raise NotFound("Artifact not found.")
    return artifact


def list_artifacts(session: Session, project: Project, status: str | None = None) -> list[dict]:
    query = select(Artifact).where(Artifact.project_id == project.id)
    if status:
        query = query.where(Artifact.status == status)
    return [artifact_view(a, project) for a in session.scalars(query.order_by(Artifact.created_at.desc()).limit(200))]


def _changes(raw: list[dict]) -> list[FactChange]:
    if not raw:
        raise Invalid("changes are required.")
    return [FactChange(field=c["field"], value=str(c["value"]), unit=c.get("unit"), status="confirmed") for c in raw]


def handle_event(session: Session, project: Project, artifact: Artifact, user_id: uuid.UUID, operation: str,
                 inputs: dict | None, changes: list[dict] | None, expected_version: int | None,
                 name: str | None) -> dict:
    inputs = inputs or {}
    if operation == "explore":
        if artifact.type != "scenario":
            raise Invalid("Only scenarios can be explored.")
        merged = {**(artifact.inputs or {}), **inputs}
        result = calculate(artifact.data["calculation_type"], merged, artifact.data.get("currency"))

    elif operation == "save_scenario":
        if artifact.type != "scenario":
            raise Invalid("Only scenarios can be saved.")
        merged = {**(artifact.inputs or {}), **inputs}
        data = calculate(artifact.data["calculation_type"], merged, artifact.data.get("currency"))
        artifact.inputs, artifact.data = merged, data
        artifact.status = "saved"
        artifact.saved_name = (name or artifact.title)[:255]
        result = {"saved": True, "scenario": data}

    elif operation in ("adopt", "submit_form"):
        if operation == "submit_form" and artifact.type != "form":
            raise Invalid("submit_form applies to form components.")
        if operation == "adopt" and artifact.type != "scenario":
            raise Invalid("adopt applies to scenarios.")
        if expected_version is None:
            raise Invalid("expected_version is required to change project facts.")
        if operation == "submit_form":
            allowed = {f["field"] for f in artifact.data["fields"]}
            for change in changes or []:
                if change.get("field") not in allowed:
                    raise Invalid(f"{change.get('field')} is not part of this form.")
        version = apply_fact_changes(
            session, project.id, _changes(changes or []), expected_version=expected_version,
            source_kind="scenario" if operation == "adopt" else "form", actor_id=user_id,
        )
        if operation == "adopt":
            artifact.status = "adopted"
            summary = ", ".join(f"{c['field']} = {c['value']}" for c in changes)
            add_decision(session, project.id, f"Adopted scenario “{artifact.title}”: {summary}", None,
                         source_kind="scenario", actor_id=user_id)
        result = {"project_version": version}

    else:
        raise Invalid("operation must be explore, save_scenario, adopt, or submit_form.")

    session.add(ArtifactEvent(artifact_id=artifact.id, user_id=user_id, operation=operation,
                              inputs={"inputs": inputs, "changes": changes or [], "name": name}, result=result))
    session.commit()
    return {"operation": operation, "result": result, "artifact": artifact_view(artifact, session.get(Project, project.id))}
