"""Company research with Gemini's Google Search grounding. Results are stored as
suggestions with sources and never overwrite what the user said."""

import json
import logging
from collections.abc import Sequence

from langchain_core.messages import HumanMessage

from ceo_backend.fields import RESEARCHABLE_COMPANY_FIELDS
from ceo_backend.llm import LLMProvider, text_of

log = logging.getLogger(__name__)

FIELD_MEANINGS = (
    "employee_count is the approximate headcount; products_services is what they sell; "
    "main_customers is who buys it; stage is one of early, growth, established, enterprise; "
    "revenue_model is how they make money; markets is the geographies served; "
    "priorities is a list of strategic goals they have publicly stated."
)


def _parse_json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in research response")
    return json.loads(text[start : end + 1])


def _grounding_sources(response) -> list[str]:
    metadata = (getattr(response, "response_metadata", None) or {}).get("grounding_metadata") or {}
    urls: list[str] = []
    for chunk in metadata.get("grounding_chunks") or []:
        uri = (chunk.get("web") or {}).get("uri")
        if uri and uri not in urls:
            urls.append(uri)
    return urls


def research_company(llm: LLMProvider, name: str, industry: str, fields: Sequence[str]) -> dict:
    wanted = [f for f in fields if f in RESEARCHABLE_COMPANY_FIELDS]
    if not wanted:
        return {"findings": {}, "sources": []}
    prompt = (
        f'Use web search to research the company "{name}" in the {industry} industry. '
        f"Reply with only a JSON object with these keys: {', '.join(wanted)}, sources. "
        f"Meanings: {FIELD_MEANINGS} "
        "Use null for anything you cannot verify from a source. Do not guess. "
        '"sources" is a list of the URLs you relied on.'
    )
    try:
        model = llm.chat("research").bind_tools([{"google_search": {}}])
        response = model.invoke([HumanMessage(content=prompt)])
        data = _parse_json_object(text_of(response.content))
    except Exception as exc:  # noqa: BLE001
        log.warning("company research failed: %s", exc)
        return {"error": "Research could not be completed.", "findings": {}, "sources": []}

    sources = [s for s in (data.pop("sources", None) or []) if isinstance(s, str)] or _grounding_sources(response)
    findings = {key: data[key] for key in wanted if data.get(key) not in (None, "", [])}
    return {"findings": findings, "sources": sources[:10]}
