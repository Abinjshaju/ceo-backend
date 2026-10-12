"""Background memory: extract durable facts, decisions and questions from each
exchange, then keep a rolling project summary.

Extraction never overwrites a confirmed value. It skips fields a tool already wrote
from the same or a later message, and it records conflicts as open questions."""

import uuid
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ceo_backend.fields import PROJECT_FIELDS, ProjectField
from ceo_backend.llm import text_of
from ceo_backend.models import Conversation, Message, OpenQuestion, Project, ProjectSummary
from ceo_backend.services import jobs
from ceo_backend.services.conversations import project_conversation_ids
from ceo_backend.services.projects import (
    FactChange,
    add_decision,
    add_question,
    apply_fact_changes,
    current_facts,
    list_decisions,
    list_questions,
    resolve_question,
)

STATUS_RANK = {"proposed": 1, "assumed": 2, "confirmed": 3, "disputed": 0}


class ExtractedFact(BaseModel):
    field: ProjectField
    value: str = Field(description="Value as stated, with currency and units")
    status: Literal["confirmed", "assumed", "proposed"] = Field(
        description="confirmed: the user stated it as true; assumed: the user called it an assumption "
        "or estimate to revisit; proposed: suggested but not agreed"
    )
    hypothetical: bool = Field(description="True for what-if or scenario values that are not actual project facts")
    source_message_id: int


class ExtractedDecision(BaseModel):
    description: str
    rationale: str | None = None
    adopted_by_user: bool = Field(description="True only if the user said this was decided")
    source_message_id: int


class ExtractedQuestion(BaseModel):
    description: str
    source_message_id: int


class AnsweredQuestion(BaseModel):
    question_id: int
    resolution: str
    source_message_id: int


class MemoryExtraction(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)
    decisions: list[ExtractedDecision] = Field(default_factory=list)
    open_questions: list[ExtractedQuestion] = Field(default_factory=list)
    answered_questions: list[AnsweredQuestion] = Field(default_factory=list)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def enqueue_memory(session: Session, conversation: Conversation, through_message_id: int) -> None:
    # Onboarding agents save every field through tools; extraction runs on project chat only.
    if conversation.project_id is None or conversation.kind != "project_chat":
        return
    jobs.enqueue(
        session, "memory_extract",
        {"conversation_id": str(conversation.id), "through_message_id": through_message_id},
        f"memory:{conversation.id}:{through_message_id}",
    )


@jobs.register("memory_extract")
def extract_memory(services, session: Session, payload: dict) -> None:
    # Lock the conversation so overlapping jobs process each message range once.
    conversation = session.scalar(
        select(Conversation).where(Conversation.id == uuid.UUID(payload["conversation_id"]))
        .with_for_update().execution_options(populate_existing=True)
    )
    if conversation is None or conversation.project_id is None:
        return
    through = int(payload["through_message_id"])
    start = conversation.memory_through_message_id or 0
    if through <= start:
        return
    messages = session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.id > start, Message.id <= through)
        .order_by(Message.id)
    ).all()
    project_id = conversation.project_id

    if messages:
        facts = current_facts(session, project_id)
        fact_lines = "\n".join(f"- {name}: {f.value} ({f.status})" for name, f in facts.items()) or "none"
        question_lines = "\n".join(f"- [{q['id']}] {q['description']}" for q in list_questions(session, project_id)) or "none"
        decision_lines = "\n".join(f"- {d['description']}" for d in list_decisions(session, project_id, limit=30)) or "none"
        transcript = "\n\n".join(f"[message {m.id}] {m.role}: {m.content}" for m in messages)
        fields = "\n".join(f"- {name}: {spec.description}" for name, spec in PROJECT_FIELDS.items())
        system = (
            "You maintain the durable memory of a project. From the new messages, extract only what they "
            "state: project facts, decisions the user said were made, new unanswered questions or missing "
            "information, and open questions that the messages answer. Mark what-if values as hypothetical. "
            "Assistant suggestions are proposed unless the user agreed. A decision is a choice about the project "
            "itself, such as scope, budget, timeline, approach, or staffing; requests to save, create, or confirm "
            "records are not decisions. Cite the source message id for each item. Skip anything already "
            f"recorded, including decisions with the same meaning.\nProject fields:\n{fields}"
        )
        context = (
            f"Current facts:\n{fact_lines}\n\nOpen questions:\n{question_lines}\n\n"
            f"Decisions:\n{decision_lines}\n\nNew messages:\n{transcript}"
        )
        model = services.llm.chat("extraction").with_structured_output(MemoryExtraction)
        result: MemoryExtraction = model.invoke([SystemMessage(content=system), HumanMessage(content=context)])
        _apply(session, project_id, result, {m.id: m for m in messages})

    conversation.memory_through_message_id = through
    jobs.enqueue(session, "summarize_project", {"project_id": str(project_id)}, f"summary:{project_id}:{through}")


def _apply(session: Session, project_id: uuid.UUID, result: MemoryExtraction, sources: dict[int, Message]) -> None:
    for item in result.facts:
        source = sources.get(item.source_message_id)
        if item.hypothetical or source is None or not item.value.strip():
            continue
        status = "proposed" if source.role == "assistant" and item.status == "confirmed" else item.status
        existing = current_facts(session, project_id).get(item.field)
        if existing is not None:
            if _normalize(existing.value) == _normalize(item.value):
                continue
            # A tool already wrote this field from this message or a later one.
            if existing.source_message_id and existing.source_message_id >= source.id:
                continue
            if existing.status == "confirmed":
                add_question(
                    session, project_id,
                    f'Possible conflict for {PROJECT_FIELDS[item.field].label.lower()}: the record says '
                    f'"{existing.value}", but message {source.id} mentions "{item.value}". Which is current?',
                    source_kind="memory", source_message_id=source.id,
                )
                continue
            if STATUS_RANK[status] < STATUS_RANK.get(existing.status, 0):
                continue
        apply_fact_changes(
            session, project_id, [FactChange(field=item.field, value=item.value, status=status)],
            expected_version=None, source_kind="memory", source_message_id=source.id,
        )

    for item in result.decisions:
        source = sources.get(item.source_message_id)
        if source is None:
            continue
        adopted = item.adopted_by_user and source.role == "user"
        add_decision(
            session, project_id, item.description, item.rationale,
            status="adopted" if adopted else "proposed", source_kind="memory", source_message_id=source.id,
        )

    for item in result.open_questions:
        if item.source_message_id in sources:
            add_question(session, project_id, item.description, source_kind="memory",
                         source_message_id=item.source_message_id)

    for item in result.answered_questions:
        question = session.get(OpenQuestion, item.question_id)
        if question and question.project_id == project_id and question.status == "open" and item.source_message_id in sources:
            resolve_question(session, project_id, item.question_id, item.resolution, item.source_message_id)


@jobs.register("summarize_project")
def summarize_project(services, session: Session, payload: dict) -> None:
    project = session.get(Project, uuid.UUID(payload["project_id"]))
    if project is None:
        return
    try:
        with session.begin_nested():
            session.add(ProjectSummary(project_id=project.id, summary="", through_message_id=None, project_version=0))
    except IntegrityError:
        pass  # another job created it first
    row = session.scalar(
        select(ProjectSummary).where(ProjectSummary.project_id == project.id)
        .with_for_update().execution_options(populate_existing=True)
    )
    messages = session.scalars(
        select(Message)
        .where(Message.conversation_id.in_(project_conversation_ids(session, project.id)),
               Message.id > (row.through_message_id or 0))
        .order_by(Message.id)
        .limit(80)
    ).all()
    if not messages:
        return
    facts = current_facts(session, project.id)
    fact_lines = "\n".join(f"- {name}: {f.value} ({f.status})" for name, f in facts.items()) or "none"
    transcript = "\n\n".join(f"{m.role}: {m.content[:2000]}" for m in messages)
    system = (
        "Update a running summary of a project conversation for a CEO's assistant. Keep it under 250 words. "
        "Cover what was established, what changed, decisions, and what is still open. Keep each fact's "
        "status: never present an assumption or proposal as confirmed. Do not add anything the messages "
        "and facts do not support."
    )
    content = (
        f"Previous summary:\n{row.summary or '(none)'}\n\nCurrent facts:\n{fact_lines}\n\n"
        f"New messages:\n{transcript}"
    )
    response = services.llm.chat("summary").invoke([SystemMessage(content=system), HumanMessage(content=content)])
    row.summary = text_of(response.content).strip()
    row.through_message_id = messages[-1].id
    row.project_version = project.version
