import json

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool

from ceo_backend.research import Researcher
from ceo_backend.store import IntakeStore

COMPANY_REQUIRED = ("name", "industry")
PROJECT_REQUIRED = ("name", "objective", "owner", "target_date", "budget")

MAX_TOOL_ROUNDS = 6
FALLBACK_REPLY = "Sorry, I couldn't finish that. Could you rephrase or try again?"


def missing_fields(data: dict, required: tuple[str, ...]) -> list[str]:
    return [field for field in required if not data.get(field)]


def intake_summary(intake: dict) -> dict:
    return {
        **intake,
        "missing_company": missing_fields(intake["company"], COMPANY_REQUIRED),
        "missing_project": missing_fields(intake["project"], PROJECT_REQUIRED),
    }


def _maybe_research(session_id: str, store: IntakeStore, research: Researcher) -> None:
    company = store.get_intake(session_id)["company"]
    if missing_fields(company, COMPANY_REQUIRED):
        return
    query = f"{company['name']}|{company['industry']}"
    if company.get("research", {}).get("query") == query:
        return
    result = research(company["name"], company["industry"])
    store.save_section(session_id, "company", {"research": {"query": query, **result}})


def make_tools(session_id: str, store: IntakeStore, research: Researcher | None = None) -> list:
    # Tools are bound to one session so the model cannot write to another intake.

    @tool
    def get_intake_state() -> dict:
        """Return saved company and project details, missing required fields, and project status."""
        return intake_summary(store.get_intake(session_id))

    @tool
    def save_company_fields(
        name: str | None = None,
        industry: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Save company details the user stated. Omit any field the user did not mention."""
        fields = {
            key: value
            for key, value in {
                "name": name,
                "industry": industry,
                "description": description,
            }.items()
            if value
        }
        store.save_section(session_id, "company", fields)
        if research:
            _maybe_research(session_id, store, research)
        return intake_summary(store.get_intake(session_id))

    @tool
    def save_project_fields(
        name: str | None = None,
        objective: str | None = None,
        owner: str | None = None,
        target_date: str | None = None,
        budget: str | None = None,
        expected_impact: str | None = None,
    ) -> dict:
        """Save project details the user stated. Omit any field the user did not mention.
        Include the currency in budget, for example "₹20 lakh"."""
        if store.get_intake(session_id)["project_status"] == "finalized":
            return {"error": "The project is already finalized and can no longer be edited."}
        fields = {
            key: value
            for key, value in {
                "name": name,
                "objective": objective,
                "owner": owner,
                "target_date": target_date,
                "budget": budget,
                "expected_impact": expected_impact,
            }.items()
            if value
        }
        store.save_section(session_id, "project", fields)
        return intake_summary(store.get_intake(session_id))

    @tool
    def finalize_project() -> dict:
        """Finalize the project. Call only after the user has confirmed the summary
        and no required fields are missing."""
        intake = store.get_intake(session_id)
        summary = intake_summary(intake)
        if summary["missing_company"] or summary["missing_project"]:
            return {"ok": False, **summary}
        store.set_project_status(session_id, "finalized")
        return {"ok": True, "project_status": "finalized"}

    return [get_intake_state, save_company_fields, save_project_fields, finalize_project]


def system_prompt(intake: dict) -> str:
    state = json.dumps(intake_summary(intake), ensure_ascii=False, indent=2)
    return f"""You are the onboarding assistant for a CEO's project workspace. Collect company and project details through natural conversation.

Rules:
- Extract every relevant detail from each answer and save it with the save_* tools before replying. Save only what the user stated; never invent values.
- Ask one concise follow-up about the most useful missing required field.
- Required company fields: {", ".join(COMPANY_REQUIRED)}. Required project fields: {", ".join(PROJECT_REQUIRED)}.
- When company details are saved, the system researches the company on the web and stores the results under company.research. Present the findings briefly, marking them as found online, and ask the user to confirm or correct them. Never copy research into the user's own fields without their confirmation.
- When nothing required is missing, summarize the company and project and ask the user to confirm. Call finalize_project only after they confirm.

Saved state:
{state}"""


def _text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in content
    )


def run_turn(
    session_id: str,
    message: str,
    llm: BaseChatModel,
    store: IntakeStore,
    research: Researcher | None = None,
) -> str:
    intake = store.get_intake(session_id)
    history: list[BaseMessage] = [
        HumanMessage(content=content) if role == "user" else AIMessage(content=content)
        for role, content in store.history(session_id)
    ]
    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt(intake)),
        *history,
        HumanMessage(content=message),
    ]

    tools = make_tools(session_id, store, research)
    tools_by_name = {t.name: t for t in tools}
    model = llm.bind_tools(tools)

    ai = AIMessage(content="")
    for _ in range(MAX_TOOL_ROUNDS):
        ai = model.invoke(messages)
        messages.append(ai)
        if not ai.tool_calls:
            break
        for call in ai.tool_calls:
            result = tools_by_name[call["name"]].invoke(call["args"])
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=call["id"],
                )
            )

    reply = FALLBACK_REPLY if ai.tool_calls else _text(ai.content)

    store.append_message(session_id, "user", message)
    store.append_message(session_id, "assistant", reply)
    return reply
