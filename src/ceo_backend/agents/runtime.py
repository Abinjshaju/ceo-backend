"""Agent execution: the tool loop, tool audit, streaming events, limits, and
citation checking. Shared by the onboarding and project agents."""

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, ToolMessage, message_chunk_to_message
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ceo_backend.errors import DomainError
from ceo_backend.llm import text_of
from ceo_backend.models import AgentRun, ToolCall, User

log = logging.getLogger(__name__)

Emit = Callable[[str, dict], None]

FALLBACK_REPLY = "Sorry, I couldn't finish that. Could you rephrase or try again?"
MAX_TOOL_RESULT_CHARS = 16_000
MODEL_CALL_TIMEOUT = 90
TRANSIENT_MARKERS = ("server disconnected", "connection reset", "timed out", "timeout", "503", "unavailable",
                     "429", "resource_exhausted", "500", "internal", "deadline")

PROGRESS_LABELS = {
    "get_project_context": "Reviewing the project",
    "search_project_history": "Searching earlier conversations",
    "list_project_documents": "Checking documents",
    "search_project_documents": "Searching documents",
    "read_document_section": "Reading a document",
    "get_document_processing_status": "Checking document status",
    "update_project_facts": "Saving project facts",
    "record_decision": "Recording a decision",
    "manage_open_question": "Updating open questions",
    "generate_decision_brief": "Writing the decision brief",
    "calculate_scenario": "Calculating a scenario",
    "present_component": "Preparing a view",
    "get_intake_state": "Checking progress",
    "save_company_fields": "Saving company details",
    "save_project_fields": "Saving project details",
    "finalize_company_profile": "Finishing the company profile",
    "finalize_project": "Creating the project",
    "suggest_answers": "Preparing suggestions",
}


@dataclass
class RunState:
    services: object
    session: Session
    run: AgentRun
    user: User
    emit: Emit | None = None
    evidence: dict[str, dict] = field(default_factory=dict)
    components: list[dict] = field(default_factory=list)
    artifact_ids: list[uuid.UUID] = field(default_factory=list)
    suggested_actions: list[dict] = field(default_factory=list)
    project_version: int | None = None
    deadline: float = 0.0

    def send(self, event: str, data: dict) -> None:
        if self.emit is not None:
            self.emit(event, data)

    def register(self, items: list[dict]) -> None:
        """Remember evidence returned to the model so its citations can be checked."""
        for item in items:
            ref = item.get("ref")
            if ref:
                self.evidence[ref] = {k: v for k, v in item.items() if k not in ("text", "snippet", "score", "matched_by", "requested")}

    def context_updated(self, **info) -> None:
        self.send("context_updated", info)


def execute_tool(state: RunState, tools: dict, call: dict) -> ToolMessage:
    name, args = call["name"], call.get("args") or {}
    state.send("progress", {"stage": PROGRESS_LABELS.get(name, "Working")})
    started = time.monotonic()
    ok = True
    try:
        tool = tools.get(name)
        if tool is None:
            raise DomainError(f"Unknown tool {name!r}.")
        result = tool.invoke(args)
    except DomainError as exc:
        state.session.rollback()
        ok, result = False, {"ok": False, "error": exc.message, **exc.details}
    except ValidationError as exc:
        state.session.rollback()
        ok, result = False, {"ok": False, "error": f"Invalid arguments: {exc.errors(include_url=False)}"}
    except Exception:  # noqa: BLE001
        state.session.rollback()
        log.exception("tool %s failed", name)
        ok, result = False, {"ok": False, "error": "The tool failed unexpectedly."}

    payload = json.dumps(result, ensure_ascii=False, default=str)
    stored = result
    if len(payload) > MAX_TOOL_RESULT_CHARS:
        payload = payload[:MAX_TOOL_RESULT_CHARS] + " …(truncated)"
        stored = {"truncated": True, "preview": payload[:2000]}
    state.session.add(ToolCall(
        run_id=state.run.id, name=name, arguments=json.loads(json.dumps(args, default=str)),
        result=json.loads(json.dumps(stored, default=str)), ok=ok,
        duration_ms=int((time.monotonic() - started) * 1000),
    ))
    state.session.commit()
    return ToolMessage(content=payload, tool_call_id=call["id"], name=name)


def _transient(exc: BaseException) -> bool:
    """Network drops, timeouts, rate limits and 5xx responses are worth one retry."""
    seen = exc
    while seen is not None:
        if isinstance(seen, (TimeoutError, ConnectionError)):
            return True
        if type(seen).__module__.startswith("httpx") and type(seen).__name__ in (
            "RemoteProtocolError", "ConnectError", "ReadError", "ReadTimeout", "ConnectTimeout", "WriteError",
        ):
            return True
        if any(marker in str(seen).lower() for marker in TRANSIENT_MARKERS):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _call_model(state: RunState, model, messages: list[BaseMessage], stream: bool, separate: bool):
    """One model call with a wall-clock limit. Returns (message, streamed_any_text).
    A call that overruns is abandoned; its thread can no longer emit events."""
    abandoned = threading.Event()
    streamed = threading.Event()
    box: dict = {}

    def run() -> None:
        try:
            if stream and state.emit is not None:
                aggregate = None
                for chunk in model.stream(messages):
                    if abandoned.is_set():
                        return
                    aggregate = chunk if aggregate is None else aggregate + chunk
                    delta = text_of(chunk.content)
                    if delta:
                        if not streamed.is_set() and separate:
                            state.send("text_delta", {"text": "\n\n"})
                        streamed.set()
                        state.send("text_delta", {"text": delta})
                if aggregate is None:
                    raise RuntimeError("The model returned an empty response.")
                box["value"] = message_chunk_to_message(aggregate)
            else:
                box["value"] = model.invoke(messages)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    remaining = state.deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("The assistant took too long to respond.")
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(min(MODEL_CALL_TIMEOUT, remaining))
    if thread.is_alive():
        abandoned.set()
        raise TimeoutError("The model did not respond in time.")
    if "error" in box:
        error = box["error"]
        error.streamed = streamed.is_set()
        raise error
    return box["value"]


def run_agent(state: RunState, llm: BaseChatModel, messages: list[BaseMessage], tools: list,
              stream: bool = False) -> str:
    """Call the model, run requested tools, and repeat until it answers. Returns the
    reply text: every round's text joined, matching what was streamed."""
    settings = state.services.settings
    by_name = {t.name: t for t in tools}
    model = llm.bind_tools(tools)
    texts: list[str] = []

    for _ in range(settings.agent_max_rounds):
        if time.monotonic() > state.deadline:
            raise TimeoutError("The assistant took too long to respond.")
        for attempt in (1, 2):
            try:
                ai = _call_model(state, model, messages, stream, separate=bool(texts))
                break
            except Exception as exc:
                if attempt == 2 or getattr(exc, "streamed", False) or not _transient(exc):
                    raise
                log.warning("model call failed (%s); retrying once", exc)

        usage = getattr(ai, "usage_metadata", None) or {}
        state.run.input_tokens += usage.get("input_tokens", 0) or 0
        state.run.output_tokens += usage.get("output_tokens", 0) or 0
        state.session.commit()

        messages.append(ai)
        text = text_of(ai.content).strip()
        if text:
            texts.append(text)
        if not ai.tool_calls:
            return "\n\n".join(texts) or FALLBACK_REPLY
        for call in ai.tool_calls:
            messages.append(execute_tool(state, by_name, call))

    return "\n\n".join(texts) or FALLBACK_REPLY


CITATION = re.compile(r"\[\s*((?:doc|msg|fact|calc|decision|question):[^\]]+?)\s*\]")
# Markers the model sometimes makes up from its context, such as [latest_brief], [summary] or [brief:2].
# They point at nothing citable, so they are removed. Markdown links and real refs are left alone.
CONTEXT_SECTIONS = ("project", "company", "facts", "decisions", "documents", "summary")
STRAY_REF = re.compile(
    r"\[\s*(?:[a-z]+(?:_[a-z0-9]+)+|" + "|".join(CONTEXT_SECTIONS)
    + r"|(?!(?:doc|msg|fact|calc|decision|question):)[a-z_]+:[^\]\s]*)\s*\](?!\()"
)


def resolve_citations(text: str, evidence: dict[str, dict]) -> tuple[str, list[dict]]:
    """Keep only citation markers that point at evidence the model actually received,
    and return the citations in order of first use."""
    citations: list[dict] = []
    seen: set[str] = set()

    def replace(match: re.Match) -> str:
        refs = [r.strip() for r in re.split(r"[,;]", match.group(1)) if r.strip()]
        valid = [r for r in refs if r in evidence]
        for ref in valid:
            if ref not in seen:
                seen.add(ref)
                citations.append(evidence[ref])
        return f"[{', '.join(valid)}]" if valid else ""

    cleaned = STRAY_REF.sub("", CITATION.sub(replace, text))
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip(), citations
