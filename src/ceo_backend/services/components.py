"""Approved interactive component types and their validation. The frontend renders
these; the model never sends executable UI."""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from ceo_backend.errors import Invalid
from ceo_backend.fields import ProjectField

Cell = str | float | int | None


class TableData(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=12)
    rows: list[list[Cell]] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _shape(self):
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError("every row needs one value per column")
        return self


class ChartPoint(BaseModel):
    x: str
    y: float = Field(allow_inf_nan=False)


class ChartSeries(BaseModel):
    name: str
    points: list[ChartPoint] = Field(min_length=1, max_length=100)


class ChartData(BaseModel):
    chart_type: Literal["bar", "line", "pie"]
    unit: str | None = None
    x_label: str | None = None
    y_label: str | None = None
    series: list[ChartSeries] = Field(min_length=1, max_length=8)


class TimelineItem(BaseModel):
    label: str
    date: str | None = None
    status: Literal["done", "in_progress", "planned", "at_risk"] | None = None
    depends_on: list[str] = Field(default_factory=list)


class TimelineData(BaseModel):
    items: list[TimelineItem] = Field(min_length=1, max_length=50)


class FormField(BaseModel):
    field: ProjectField
    label: str
    input_type: Literal["text", "number", "date"] = "text"
    current_value: str | None = None


class FormData(BaseModel):
    fields: list[FormField] = Field(min_length=1, max_length=10)
    submit_label: str = "Save"


class EvidencePanelData(BaseModel):
    claim: str
    refs: list[str] = Field(min_length=1, max_length=10)


PRESENTABLE = {
    "table": TableData,
    "chart": ChartData,
    "timeline": TimelineData,
    "form": FormData,
    "evidence_panel": EvidencePanelData,
}
# Created by their own tools rather than present_component.
TOOL_CREATED = ("scenario", "decision_brief")
COMPONENT_TYPES = (*PRESENTABLE, *TOOL_CREATED)
NEEDS_SOURCES = ("table", "chart", "evidence_panel")


def validate_component(component_type: str, title: str, data_json: str, source_refs: list[str],
                       known_refs: set[str]) -> dict:
    """Validate a component the model wants to show. Raises Invalid with a message the
    model can act on."""
    schema = PRESENTABLE.get(component_type)
    if schema is None:
        raise Invalid(f"Unsupported component type {component_type!r}. Use one of {', '.join(PRESENTABLE)}.")
    if not title.strip():
        raise Invalid("A component needs a title.")
    try:
        raw = json.loads(data_json) if isinstance(data_json, str) else data_json
        data = schema.model_validate(raw).model_dump()
    except (json.JSONDecodeError, ValidationError) as exc:
        raise Invalid(f"Invalid {component_type} data: {exc}") from exc

    refs = list(dict.fromkeys(source_refs or []))
    if component_type == "evidence_panel":
        refs = list(dict.fromkeys([*refs, *data["refs"]]))
    unknown = [ref for ref in refs if ref not in known_refs]
    if unknown:
        raise Invalid(
            f"Unknown source refs {unknown}. Cite only refs returned by tools or listed in the project context."
        )
    if component_type in NEEDS_SOURCES and not refs:
        raise Invalid(f"A {component_type} must cite the sources of its values in source_refs.")
    return {"type": component_type, "title": title.strip()[:255], "data": data, "source_refs": refs}
