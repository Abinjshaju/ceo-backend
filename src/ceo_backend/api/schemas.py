import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from ceo_backend.fields import ProjectField


class OnboardingStart(BaseModel):
    kind: Literal["company", "project"]


class ChatMessageIn(BaseModel):
    client_message_id: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=20_000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


class CompanyPatch(BaseModel):
    expected_version: int
    fields: dict[str, Any] = Field(default_factory=dict)
    unknown_fields: list[str] = Field(default_factory=list)
    # Mark the profile complete (finishing onboarding from the form instead of the chat).
    complete: bool = False


class FactChangeIn(BaseModel):
    field: ProjectField
    value: str = Field(min_length=1, max_length=4000)
    unit: str | None = Field(default=None, max_length=50)
    status: Literal["confirmed", "proposed", "assumed"] = "confirmed"
    effective_date: str | None = None


class ProjectPatch(BaseModel):
    expected_version: int
    changes: list[FactChangeIn] = Field(default_factory=list)
    status: Literal["active", "archived"] | None = None


class FinalizeIn(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=100)


class UploadUrlIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = ""
    size_bytes: int = Field(gt=0)
    document_id: uuid.UUID | None = None


class CompleteUploadIn(BaseModel):
    version_id: uuid.UUID


class ArtifactChange(BaseModel):
    field: ProjectField
    value: str = Field(min_length=1, max_length=4000)
    unit: str | None = None


class ArtifactEventIn(BaseModel):
    operation: Literal["explore", "save_scenario", "adopt", "submit_form"]
    inputs: dict[str, Any] | None = None
    changes: list[ArtifactChange] | None = None
    expected_version: int | None = None
    name: str | None = Field(default=None, max_length=255)
