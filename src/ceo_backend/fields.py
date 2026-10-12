"""Field definitions owned by the application. Agents choose wording and order;
these definitions decide what is required and when intake is complete."""

from dataclasses import dataclass
from typing import Literal

Stage = Literal["early", "growth", "established", "enterprise"]
FactStatus = Literal["confirmed", "proposed", "assumed", "disputed"]


@dataclass(frozen=True)
class FieldSpec:
    label: str
    description: str
    required: bool = True
    unknown_allowed: bool = False


COMPANY_FIELDS: dict[str, FieldSpec] = {
    "name": FieldSpec("Company name", "the company's name"),
    "employee_count": FieldSpec(
        "Employee count", "approximate number of employees", unknown_allowed=True
    ),
    "products_services": FieldSpec("Products and services", "what the company sells"),
    "main_customers": FieldSpec("Main customers", "who buys from the company"),
    "industry": FieldSpec("Industry", "the industry the company operates in"),
    "stage": FieldSpec("Stage", "early, growth, established, or enterprise"),
    "revenue_model": FieldSpec(
        "Revenue model", "how the company makes money", unknown_allowed=True
    ),
    "markets": FieldSpec(
        "Markets", "geographies or markets served", unknown_allowed=True
    ),
    "priorities": FieldSpec(
        "12-month priorities",
        "top priorities for the next 12 months, such as growth, margins, retention, or cost",
    ),
}

# Fields web research may suggest. Name and industry are the search inputs.
RESEARCHABLE_COMPANY_FIELDS = (
    "employee_count",
    "products_services",
    "main_customers",
    "stage",
    "revenue_model",
    "markets",
    "priorities",
)

PROJECT_FIELDS: dict[str, FieldSpec] = {
    "name": FieldSpec("Project name", "the project's name"),
    "description": FieldSpec("Description", "what the project does"),
    "objective": FieldSpec("Company objective", "which company objective it supports"),
    "investment": FieldSpec(
        "Investment", "required investment or budget, with currency and units",
        unknown_allowed=True,
    ),
    "financial_impact": FieldSpec(
        "Financial impact",
        "expected revenue, savings, margin, or cash-flow impact, with currency and timeframe",
        unknown_allowed=True,
    ),
    "growth_outcomes": FieldSpec(
        "Growth outcomes",
        "expected ARR, customers, adoption, market share, or expansion",
        unknown_allowed=True,
    ),
    "owner": FieldSpec("Delivery owner", "who owns delivery"),
    "target_date": FieldSpec("Target date", "target delivery or launch date"),
    "customer_evidence": FieldSpec(
        "Customer evidence", "evidence customers want this", required=False,
        unknown_allowed=True,
    ),
    "dependencies": FieldSpec(
        "Dependencies", "teams, vendors, or projects it depends on", required=False,
        unknown_allowed=True,
    ),
    "risks": FieldSpec("Major risks", "the main risks", unknown_allowed=True),
    # Captured during project chat rather than intake.
    "delivery_status": FieldSpec("Delivery status", "current delivery status", required=False),
    "team_capacity": FieldSpec(
        "Team capacity", "people or capacity assigned to delivery", required=False
    ),
}

ProjectField = Literal[
    "name",
    "description",
    "objective",
    "investment",
    "financial_impact",
    "growth_outcomes",
    "owner",
    "target_date",
    "customer_evidence",
    "dependencies",
    "risks",
    "delivery_status",
    "team_capacity",
]

UNKNOWN = "unknown"


def missing_fields(values: dict, specs: dict[str, FieldSpec]) -> list[str]:
    """Required fields with no value. An explicit 'unknown' counts as answered
    only where the field allows it."""
    missing = []
    for name, spec in specs.items():
        if not spec.required:
            continue
        entry = values.get(name)
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value in (None, "", []):
            missing.append(name)
        elif value == UNKNOWN and not spec.unknown_allowed:
            missing.append(name)
    return missing
