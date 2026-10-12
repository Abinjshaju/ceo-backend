import json
from collections.abc import Callable

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

RESEARCH_FIELDS = (
    "description",
    "website",
    "headquarters",
    "founded",
    "employee_count",
    "key_products",
    "leadership",
)

Researcher = Callable[[str, str], dict]


def _text(content: str | list) -> str:
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in content
    )


def _parse_json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in research response")
    return json.loads(text[start : end + 1])


def _sources(response) -> list[str]:
    metadata = response.response_metadata.get("grounding_metadata") or {}
    urls = []
    for chunk in metadata.get("grounding_chunks") or []:
        uri = (chunk.get("web") or {}).get("uri")
        if uri and uri not in urls:
            urls.append(uri)
    return urls


def make_gemini_researcher(llm: BaseChatModel) -> Researcher:
    grounded = llm.bind_tools([{"google_search": {}}])

    def research(name: str, industry: str) -> dict:
        prompt = (
            f'Use web search to research the company "{name}" in the {industry} industry. '
            f"Reply with only a JSON object with these keys: {', '.join(RESEARCH_FIELDS)}. "
            "Use null for anything you cannot verify. Do not guess. "
            'Use "leadership" as a list of strings like "Name, Title". '
            'Add a "sources" key with the URLs you relied on.'
        )
        try:
            response = grounded.invoke([HumanMessage(content=prompt)])
            findings = _parse_json_object(_text(response.content))
        except Exception as exc:
            return {"error": f"Research failed: {exc}"}

        sources = findings.pop("sources", None) or _sources(response)
        findings = {key: findings.get(key) for key in RESEARCH_FIELDS if findings.get(key)}
        return {"findings": findings, "sources": sources}

    return research
