"""One conversational turn, for any agent: idempotent message handling, run status,
the agent loop, citation checks, persistence, and follow-up memory jobs."""

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from ceo_backend.agents.runtime import Emit, RunState, resolve_citations, run_agent
from ceo_backend.db import utcnow
from ceo_backend.errors import Conflict, DomainError, Invalid
from ceo_backend.models import AgentRun, Artifact, Conversation, Message, User
from ceo_backend.services.conversations import add_message, find_message, run_for_message
from ceo_backend.services.memory import enqueue_memory

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 20_000


@dataclass
class TurnSpec:
    kind: str
    prompt_version: str
    conversation: Conversation
    build: Callable[[RunState, Message], tuple[list, list]]
    extra: Callable[[Session], dict]


def response_payload(reply: Message, extra: dict) -> dict:
    return {
        "schema_version": "1",
        "message_id": reply.id,
        "client_message_id": reply.client_message_id,
        "run_id": str(reply.run_id) if reply.run_id else None,
        "text": reply.content,
        "citations": reply.citations or [],
        "components": reply.components or [],
        "suggested_actions": reply.suggested_actions or [],
        **extra,
    }


def user_error(exc: Exception) -> str:
    if isinstance(exc, DomainError):
        return exc.message
    if isinstance(exc, TimeoutError):
        return "The assistant took too long to respond. Please try again."
    return "The assistant could not complete this request. Please try again."


def run_turn(services, session: Session, user: User, spec: TurnSpec, client_message_id: str, content: str,
             document_ids: list[str] | None = None, emit: Emit | None = None, stream: bool = False) -> dict:
    content = (content or "").strip()
    if not content:
        raise Invalid("The message is empty.")
    if len(content) > MAX_MESSAGE_CHARS:
        raise Invalid(f"Messages are limited to {MAX_MESSAGE_CHARS} characters.")
    if not client_message_id or len(client_message_id) > 100:
        raise Invalid("client_message_id is required (up to 100 characters).")
    conversation = spec.conversation

    existing = find_message(session, conversation.id, client_message_id, "user")
    if existing is not None:
        if existing.content != content:
            raise Conflict("This client_message_id was already used for a different message.")
        reply = find_message(session, conversation.id, client_message_id, "assistant")
        if reply is not None:
            payload = response_payload(reply, spec.extra(session))
            if emit:
                emit("completed", payload)
            return payload
        previous = run_for_message(session, existing.id)
        limit = timedelta(seconds=services.settings.agent_time_limit_seconds + 30)
        if previous is not None and previous.status == "running" and utcnow() - previous.created_at < limit:
            raise Conflict("This message is still being processed.", run_id=str(previous.id))
        user_message = existing
    else:
        user_message = add_message(
            session, conversation.id, client_message_id, "user", content,
            author_id=user.id, document_ids=document_ids or [],
        )
        session.commit()

    run = AgentRun(
        id=uuid.uuid4(), workspace_id=conversation.workspace_id, conversation_id=conversation.id,
        project_id=conversation.project_id, kind=spec.kind, status="running",
        model=services.llm.model_name, prompt_version=spec.prompt_version, user_message_id=user_message.id,
    )
    session.add(run)
    session.commit()

    state = RunState(
        services=services, session=session, run=run, user=user, emit=emit,
        deadline=time.monotonic() + services.settings.agent_time_limit_seconds,
    )
    state.send("run_started", {"run_id": str(run.id), "user_message_id": user_message.id})
    started = time.monotonic()
    try:
        llm = services.llm.chat("agent")
        messages, tools = spec.build(state, user_message)
        text = run_agent(state, llm, messages, tools, stream=stream)
        cleaned, citations = resolve_citations(text, state.evidence)
        cited = {item["ref"] for item in citations}
        for component in state.components:
            for item in component.get("citations", []):
                if item["ref"] not in cited:
                    citations.append(item)
                    cited.add(item["ref"])
        reply = add_message(
            session, conversation.id, client_message_id, "assistant", cleaned, run_id=run.id,
            citations=citations, components=state.components, suggested_actions=state.suggested_actions,
        )
        session.flush()
        for artifact_id in state.artifact_ids:
            artifact = session.get(Artifact, artifact_id)
            if artifact is not None:
                artifact.message_id = reply.id
        run.status = "completed"
        run.assistant_message_id = reply.id
        run.finished_at = utcnow()
        enqueue_memory(session, conversation, reply.id)
        session.commit()
    except Exception as exc:
        session.rollback()
        failed = session.get(AgentRun, run.id)
        failed.status, failed.error, failed.finished_at = "failed", str(exc)[:2000], utcnow()
        session.commit()
        log.warning("run %s failed after %.1fs: %s", run.id, time.monotonic() - started, exc)
        state.send("failed", {"run_id": str(run.id), "error": user_error(exc), "retryable": True})
        raise

    log.info("run %s completed in %.1fs (%s in / %s out tokens)", run.id, time.monotonic() - started,
             run.input_tokens, run.output_tokens)
    payload = response_payload(reply, spec.extra(session))
    payload["document_picker"] = state.document_picker
    state.send("completed", payload)
    return payload
