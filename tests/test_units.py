import pytest

from ceo_backend.agents.runtime import resolve_citations
from ceo_backend.errors import Invalid
from ceo_backend.fields import COMPANY_FIELDS, missing_fields
from ceo_backend.services.components import validate_component
from ceo_backend.services.extraction import RawSection, chunk_sections
from ceo_backend.services.retrieval import rrf
from ceo_backend.services.scenarios import calculate


def test_roi_and_payback():
    roi = calculate("roi", {"investment": 20, "annual_benefit": 10, "years": 3})
    assert roi["outputs"] == {"total_benefit": 30, "net_return": 10, "roi_percent": 50.0}
    payback = calculate("payback_period", {"investment": 24, "monthly_benefit": 2})
    assert payback["outputs"]["payback_months"] == 12


def test_npv_discounts_each_year():
    result = calculate("npv", {"investment": 100, "cash_flows": [110], "discount_rate_percent": 10})
    assert result["outputs"]["npv"] == 0.0


def test_budget_change_with_scaling_benefit():
    result = calculate("budget_change", {"investment": 20, "new_investment": 10, "annual_benefit": 60, "years": 1,
                                         "benefit_scales_with_budget": True})
    assert result["outputs"]["annual_benefit_after"] == 30
    assert result["outputs"]["roi_percent_before"] == result["outputs"]["roi_percent_after"] == 200.0


@pytest.mark.parametrize("calc,inputs", [
    ("roi", {"investment": 0, "annual_benefit": 1, "years": 1}),
    ("roi", {"investment": 1}),
    ("payback_period", {"investment": 1, "monthly_benefit": "lots"}),
    ("mystery", {}),
])
def test_invalid_calculations_are_rejected(calc, inputs):
    with pytest.raises(Invalid):
        calculate(calc, inputs)


def test_citations_keep_only_known_refs():
    text, citations = resolve_citations(
        "A [doc:1]. B [doc:2, msg:3]. C [fact:owner; doc:9].",
        {"doc:1": {"ref": "doc:1"}, "msg:3": {"ref": "msg:3"}, "fact:owner": {"ref": "fact:owner"}},
    )
    assert text == "A [doc:1]. B [msg:3]. C [fact:owner]."
    assert [c["ref"] for c in citations] == ["doc:1", "msg:3", "fact:owner"]


def test_made_up_markers_are_removed():
    text, citations = resolve_citations(
        "Refresh the brief [latest_brief]. Per [summary], risks [brief:2] remain [doc:1]. "
        "See [the plan](https://example.com) and - [x] done.",
        {"doc:1": {"ref": "doc:1"}},
    )
    assert text == "Refresh the brief. Per, risks remain [doc:1]. See [the plan](https://example.com) and - [x] done."
    assert [c["ref"] for c in citations] == ["doc:1"]


def test_components_require_sources_and_valid_shapes():
    with pytest.raises(Invalid):
        validate_component("table", "T", '{"columns": ["a"], "rows": [["1", "2"]]}', ["doc:1"], {"doc:1"})
    with pytest.raises(Invalid):
        validate_component("table", "T", '{"columns": ["a"], "rows": [["1"]]}', ["doc:7"], {"doc:1"})
    with pytest.raises(Invalid):
        validate_component("script", "T", "{}", [], set())
    ok = validate_component("timeline", "T", '{"items": [{"label": "Launch", "date": "2026-12-15"}]}', [], set())
    assert ok["data"]["items"][0]["label"] == "Launch"


def test_tables_keep_their_header_in_every_chunk():
    rows = "\n".join(f"row {i} | {'x' * 50}" for i in range(100))
    chunks = chunk_sections([RawSection(text=rows, table_header="name | value", sheet="S")], target=500)
    assert len(chunks) > 3
    assert all(c.text.startswith("name | value\n") and c.sheet == "S" for c in chunks)


def test_long_text_is_split_with_overlap():
    paragraph = " ".join(f"Sentence number {i} is here." for i in range(200))
    chunks = chunk_sections([RawSection(text=paragraph, heading="H", page=3)], target=400, overlap=80)
    assert len(chunks) > 5
    assert all(len(c.text) <= 500 and c.heading == "H" and c.page == 3 for c in chunks)
    assert chunks[1].text.split("\n\n")[0] in chunks[0].text


def test_rrf_rewards_agreement():
    fused = rrf([[1, 2, 3], [3, 1, 4]])
    assert [item for item, _ in fused][:2] == [1, 3]


def test_unknown_counts_only_where_allowed():
    values = {name: "x" for name in COMPANY_FIELDS} | {"employee_count": "unknown", "name": "unknown"}
    assert missing_fields(values, COMPANY_FIELDS) == ["name"]
