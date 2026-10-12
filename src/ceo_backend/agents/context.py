"""Context assembly within a character budget. Current authoritative facts come
first, then decisions, open questions, the rolling summary, and documents."""

import json
import uuid

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy.orm import Session

from ceo_backend.agents.runtime import RunState
from ceo_backend.models import Project, ProjectSummary
from ceo_backend.services.company import get_profile, profile_values
from ceo_backend.services.conversations import recent_messages
from ceo_backend.services.documents import document_view, list_documents
from ceo_backend.services.projects import (
    assessment_view,
    facts_view,
    latest_assessment,
    list_decisions,
    list_questions,
    missing_project_fields,
)

HISTORY_LIMIT = 30


def decision_evidence(decision: dict) -> dict:
    return {"ref": f"decision:{decision['id']}", "kind": "decision", "decision_id": decision["id"],
            "description": decision["description"], "status": decision["status"]}


def question_evidence(question: dict) -> dict:
    return {"ref": f"question:{question['id']}", "kind": "question", "question_id": question["id"],
            "description": question["description"], "status": question["status"]}


def company_context(session: Session, workspace_id: uuid.UUID) -> dict:
    profile = get_profile(session, workspace_id)
    return {"status": profile.status, **profile_values(profile)}


def project_context(session: Session, project: Project, state: RunState | None = None) -> dict:
    facts = facts_view(session, project.id)
    fact_refs = []
    compact_facts = {}
    for name, fact in facts.items():
        ref = f"fact:{name}"
        compact_facts[name] = {
            "value": fact["value"],
            "unit": fact["unit"],
            "status": fact["status"],
            "ref": ref,
            "source": fact["source"].get("ref") or fact["source"]["kind"],
        }
        fact_refs.append({"ref": ref, "kind": "fact", "field": name, "value": fact["value"],
                          "status": fact["status"], "source": fact["source"]})
    if state is not None:
        state.register(fact_refs)
        state.project_version = project.version

    decisions = list_decisions(session, project.id, limit=25)
    questions = list_questions(session, project.id)[:30]
    if state is not None:
        state.register([decision_evidence(d) for d in decisions] + [question_evidence(q) for q in questions])

    summary = session.get(ProjectSummary, project.id)
    brief = assessment_view(latest_assessment(session, project.id), project)
    documents = [document_view(session, d) for d in list_documents(session, project.workspace_id, project.id, True)]
    return {
        "project": {"id": str(project.id), "name": project.name, "status": project.status, "version": project.version},
        "company": company_context(session, project.workspace_id),
        "facts": compact_facts,
        "missing_required_fields": missing_project_fields(session, project.id),
        "decisions": [
            {"ref": f"decision:{d['id']}", "description": d["description"], "rationale": d["rationale"],
             "status": d["status"]}
            for d in decisions
        ],
        "open_questions": [{"id": q["id"], "ref": f"question:{q['id']}", "description": q["description"]} for q in questions],
        "summary": summary.summary if summary and summary.summary else None,
        "documents": [
            {"document_id": d["id"], "filename": d["filename"], "scope": d["scope"], "status": d["status"],
             "searchable": d["searchable"]}
            for d in documents
        ],
        "latest_brief": None if brief is None else {
            "score": brief["content"].get("score"),
            "feasibility": brief["content"].get("feasibility"),
            "outdated": brief["outdated"],
        },
    }


def render_context(context: dict, budget: int) -> str:
    """Serialize context, trimming the least important parts until it fits."""
    trimmed = dict(context)
    text = json.dumps(trimmed, ensure_ascii=False, indent=1)
    steps = [
        ("decisions", lambda v: v[-10:]),
        ("open_questions", lambda v: v[:15]),
        ("documents", lambda v: v[:20]),
        ("summary", lambda v: (v or "")[:1500] or None),
        ("decisions", lambda v: v[-5:]),
        ("company", lambda v: {k: v[k] for k in list(v)[:6]}),
    ]
    for key, shrink in steps:
        if len(text) <= budget:
            break
        if trimmed.get(key):
            trimmed[key] = shrink(trimmed[key])
            text = json.dumps(trimmed, ensure_ascii=False, indent=1)
    return text[:budget]


def history(session: Session, conversation_ids: list[uuid.UUID], exclude_ids: set[int],
            char_budget: int = 12_000) -> list[BaseMessage]:
    """Recent dialogue, newest kept first when the budget runs out."""
    rows = recent_messages(session, conversation_ids, limit=HISTORY_LIMIT, exclude_ids=exclude_ids)
    kept, used = [], 0
    for message in reversed(rows):
        cost = len(message.content)
        if kept and used + cost > char_budget:
            break
        kept.append(message)
        used += cost
    ordered = list(reversed(kept))
    while ordered and ordered[0].role != "user":
        ordered.pop(0)  # the model expects the dialogue to open with the user
    return [HumanMessage(content=m.content) if m.role == "user" else AIMessage(content=m.content) for m in ordered]
