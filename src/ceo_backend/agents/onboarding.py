"""The onboarding agent. Company mode builds the company profile; project mode
captures a first or new project. Required fields and completion rules come from
ceo_backend.fields; the agent decides wording and order."""

import json
from typing import Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from sqlalchemy.orm import Session

from ceo_backend.agents.context import history
from ceo_backend.agents.runtime import RunState
from ceo_backend.db import utcnow
from ceo_backend.errors import Conflict, Invalid
from ceo_backend.fields import COMPANY_FIELDS, PROJECT_FIELDS, RESEARCHABLE_COMPANY_FIELDS, UNKNOWN, Stage
from ceo_backend.models import Message, OnboardingSession, Project
from ceo_backend.services import company as company_service
from ceo_backend.services.company import complete_profile, get_profile
from ceo_backend.services.intake import company_intake_state, project_intake_state
from ceo_backend.services.intake import finalize_project as finalize_project_service
from ceo_backend.services.projects import FactChange, apply_fact_changes
from ceo_backend.services.research import research_company

PROMPT_VERSION = "onboarding-1"

CompanyUnknown = Literal["employee_count", "revenue_model", "markets"]
ProjectUnknown = Literal["investment", "financial_impact", "growth_outcomes", "risks", "customer_evidence", "dependencies"]


def _field_list(specs) -> str:
    lines = []
    for name, spec in specs.items():
        notes = []
        if not spec.required:
            notes.append("optional")
        if spec.unknown_allowed:
            notes.append('may be "unknown"')
        lines.append(f"- {name}: {spec.description}" + (f" ({', '.join(notes)})" if notes else ""))
    return "\n".join(lines)


COMMON_RULES = """How to work
- Extract every relevant detail from each answer and save it before you reply. One answer can fill several fields. Accept reasonable approximations. Save only what the user said; never invent values.
- When the user does not know a field that allows it, save the field in unknown_fields.
- Ask one short question about the most useful missing information next. You may combine closely related fields.
- When it would save typing, offer two to four likely answers with suggest_answers, built from what you already know.
- If an answer is vague, ambiguous, or conflicts with what is saved, ask a clarifying question instead of guessing. Explicit corrections can be saved directly.
- Be warm and brief: plain language, about 80 words or fewer per reply."""

COMPANY_PROMPT = """You are the onboarding guide in a CEO's decision workspace. Set up the company profile through a natural conversation. Today is {today}.

Fields:
{fields}

{rules}
- After the company name and industry are saved, the system researches the company online. Present any research findings briefly as "found online", with sources, and ask the user to confirm or correct them. Save a researched value only after the user confirms it.
- When nothing required is missing, summarize the profile and ask the user to confirm. After they confirm, call finalize_company_profile, then say the next step is setting up their first project.

Current state:
{state}"""

PROJECT_PROMPT = """You are the onboarding guide in a CEO's decision workspace. Capture a project through a natural conversation. Today is {today}; suggested dates must be in the future.

Fields:
{fields}

{rules}
- Keep currency and units with investment and impact values (for example "₹20 lakh" or "$1.2M ARR by Q4").
- Ask whether there are supporting documents. The user can upload files in the app or answer "no documents"; record the answer with save_project_fields(documents=...). Uploaded files may still be processing: never describe them as analyzed.
- When nothing required is missing, summarize the captured facts and any unknowns, and ask the user to confirm. After they confirm, call finalize_project and tell them a decision brief is being prepared.

Company context:
{company}

Current state:
{state}"""


def _suggest_tool(state: RunState):
    @tool
    def suggest_answers(options: list[str]) -> dict:
        """Offer two to four short answers the user can tap instead of typing."""
        cleaned = [o.strip()[:80] for o in options if o and o.strip()][:4]
        state.suggested_actions = [{"type": "reply", "label": o} for o in cleaned]
        return {"ok": True, "options": cleaned}

    return suggest_answers


def make_company_tools(state: RunState, onboarding: OnboardingSession, user_message: Message) -> list:
    session = state.session
    workspace_id = onboarding.workspace_id

    @tool
    def get_intake_state() -> dict:
        """Return the saved company profile, research findings, and missing required fields."""
        return company_intake_state(session, onboarding)

    @tool
    def save_company_fields(
        name: str | None = None,
        employee_count: str | None = None,
        products_services: str | None = None,
        main_customers: str | None = None,
        industry: str | None = None,
        stage: Stage | None = None,
        revenue_model: str | None = None,
        markets: str | None = None,
        priorities: list[str] | None = None,
        unknown_fields: list[CompanyUnknown] | None = None,
    ) -> dict:
        """Save company details the user stated. Omit anything they did not mention."""
        values = {
            "name": name, "employee_count": employee_count, "products_services": products_services,
            "main_customers": main_customers, "industry": industry, "stage": stage,
            "revenue_model": revenue_model, "markets": markets, "priorities": priorities,
        }
        profile = company_service.save_company_fields(
            session, workspace_id, values, source="chat", source_message_id=user_message.id,
            unknown_fields=list(unknown_fields or []),
        )
        session.commit()
        _maybe_research(state, profile)
        state.context_updated(company_version=profile.version)
        return company_intake_state(session, onboarding)

    @tool
    def finalize_company_profile() -> dict:
        """Mark the company profile complete. Call only after the user confirms the summary."""
        complete_profile(session, workspace_id)
        onboarding.status = "completed"
        onboarding.completed_at = utcnow()
        session.commit()
        state.context_updated(company_status="complete")
        return {"ok": True, "status": "complete", "next_step": "project_onboarding"}

    return [get_intake_state, save_company_fields, finalize_company_profile, _suggest_tool(state)]


def _maybe_research(state: RunState, profile) -> None:
    settings = state.services.settings
    values = {k: v["value"] for k, v in (profile.fields or {}).items()}
    if not settings.research_enabled or not values.get("name") or not values.get("industry"):
        return
    query = f"{values['name']}|{values['industry']}"
    if (profile.research or {}).get("query") == query:
        return
    missing = [f for f in RESEARCHABLE_COMPANY_FIELDS if values.get(f) in (None, "", [], UNKNOWN)]
    result = research_company(state.services.llm, values["name"], values["industry"], missing)
    profile.research = {"query": query, **result}
    state.session.commit()


def make_project_tools(state: RunState, onboarding: OnboardingSession, user_message: Message) -> list:
    session = state.session
    project_id = onboarding.project_id

    @tool
    def get_intake_state() -> dict:
        """Return the captured project facts, missing required fields, and attached documents."""
        return project_intake_state(session, onboarding)

    @tool
    def save_project_fields(
        name: str | None = None,
        description: str | None = None,
        objective: str | None = None,
        investment: str | None = None,
        financial_impact: str | None = None,
        growth_outcomes: str | None = None,
        owner: str | None = None,
        target_date: str | None = None,
        customer_evidence: str | None = None,
        dependencies: str | None = None,
        risks: str | None = None,
        unknown_fields: list[ProjectUnknown] | None = None,
        documents: Literal["none", "attached"] | None = None,
    ) -> dict:
        """Save project details the user stated. Omit anything they did not mention. Use documents to
        record whether they have supporting documents."""
        values = {
            "name": name, "description": description, "objective": objective, "investment": investment,
            "financial_impact": financial_impact, "growth_outcomes": growth_outcomes, "owner": owner,
            "target_date": target_date, "customer_evidence": customer_evidence,
            "dependencies": dependencies, "risks": risks,
        }
        changes = [FactChange(field=k, value=v) for k, v in values.items() if v and str(v).strip()]
        changes += [FactChange(field=f, value=UNKNOWN) for f in (unknown_fields or []) if not values.get(f)]
        project = session.get(Project, project_id)
        if project.status != "draft":
            raise Conflict("This project is already active. Changes now go through project chat.")
        if changes:
            try:
                state.project_version = apply_fact_changes(
                    session, project_id, changes, expected_version=state.project_version,
                    source_kind="chat", source_message_id=user_message.id, actor_id=state.user.id,
                )
            except Conflict:
                # Another tab or request edited the draft; reload and apply on top of it.
                state.project_version = session.get(Project, project_id, populate_existing=True).version
                state.project_version = apply_fact_changes(
                    session, project_id, changes, expected_version=state.project_version,
                    source_kind="chat", source_message_id=user_message.id, actor_id=state.user.id,
                )
        if documents:
            onboarding.state = {**(onboarding.state or {}), "documents": documents}
        if not changes and not documents:
            raise Invalid("Nothing to save.")
        session.commit()
        state.context_updated(project_version=state.project_version)
        return project_intake_state(session, onboarding)

    @tool
    def finalize_project() -> dict:
        """Create the project from the captured details. Call only after the user confirms the summary."""
        result = finalize_project_service(session, project_id, f"onboarding:{onboarding.id}", onboarding)
        state.context_updated(project_status="active")
        return result

    return [get_intake_state, save_project_fields, finalize_project, _suggest_tool(state)]



def build_onboarding_turn(state: RunState, onboarding: OnboardingSession,
                          user_message: Message) -> tuple[list[BaseMessage], list]:
    session: Session = state.session
    if onboarding.kind == "company":
        current = company_intake_state(session, onboarding)
        system = COMPANY_PROMPT.format(
            fields=_field_list(COMPANY_FIELDS), rules=COMMON_RULES, today=utcnow().date().isoformat(),
            state=json.dumps(current, ensure_ascii=False, indent=1),
        )
        tools = make_company_tools(state, onboarding, user_message)
    else:
        project = session.get(Project, onboarding.project_id)
        state.project_version = project.version
        profile = get_profile(session, onboarding.workspace_id)
        company = {k: v["value"] for k, v in (profile.fields or {}).items()}
        system = PROJECT_PROMPT.format(
            fields=_field_list({k: v for k, v in PROJECT_FIELDS.items() if k not in ("delivery_status", "team_capacity")}),
            rules=COMMON_RULES, today=utcnow().date().isoformat(),
            company=json.dumps(company, ensure_ascii=False),
            state=json.dumps(project_intake_state(session, onboarding), ensure_ascii=False, indent=1),
        )
        tools = make_project_tools(state, onboarding, user_message)
    messages = [
        SystemMessage(content=system),
        *history(session, [onboarding.conversation_id], exclude_ids={user_message.id}),
        HumanMessage(content=user_message.content),
    ]
    return messages, tools
