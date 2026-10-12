import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ceo_backend.db import utcnow
from ceo_backend.errors import Conflict, Invalid
from ceo_backend.fields import COMPANY_FIELDS, UNKNOWN, missing_fields
from ceo_backend.models import CompanyProfile

STAGES = ("early", "growth", "established", "enterprise")


def get_profile(session: Session, workspace_id: uuid.UUID, lock: bool = False) -> CompanyProfile:
    query = select(CompanyProfile).where(CompanyProfile.workspace_id == workspace_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    profile = session.scalar(query)
    if profile is None:
        profile = CompanyProfile(workspace_id=workspace_id, fields={}, status="draft", version=0)
        session.add(profile)
        session.flush()
    return profile


def _clean_value(name: str, value):
    if name == "priorities":
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",")]
        if not isinstance(value, list):
            raise Invalid("priorities must be a list of strings.")
        value = [str(item).strip() for item in value if str(item).strip()][:10]
        return value or None
    value = str(value).strip()
    if name == "stage":
        value = value.lower().removesuffix(" stage")
        if value not in STAGES:
            raise Invalid("stage must be early, growth, established, or enterprise.")
    return value or None


def save_company_fields(
    session: Session,
    workspace_id: uuid.UUID,
    values: dict,
    *,
    source: str,
    source_message_id: int | None = None,
    unknown_fields: list[str] | None = None,
    expected_version: int | None = None,
) -> CompanyProfile:
    profile = get_profile(session, workspace_id, lock=True)
    if expected_version is not None and profile.version != expected_version:
        raise Conflict("The company profile changed since it was loaded.", current_version=profile.version)

    updates: dict = {}
    for name, raw in values.items():
        if raw is None:
            continue
        if name not in COMPANY_FIELDS:
            raise Invalid(f"Unknown company field {name!r}.")
        cleaned = _clean_value(name, raw)
        if cleaned is not None:
            updates[name] = cleaned
    for name in unknown_fields or []:
        spec = COMPANY_FIELDS.get(name)
        if spec is None:
            raise Invalid(f"Unknown company field {name!r}.")
        if not spec.unknown_allowed:
            raise Invalid(f"{name} cannot be marked unknown.")
        updates.setdefault(name, UNKNOWN)

    if not updates:
        return profile

    fields = dict(profile.fields or {})
    now = utcnow().isoformat()
    for name, value in updates.items():
        fields[name] = {
            "value": value,
            "status": "confirmed",
            "source": source,
            "source_message_id": source_message_id,
            "updated_at": now,
        }
    profile.fields = fields
    profile.version += 1
    session.flush()
    return profile


def profile_values(profile: CompanyProfile) -> dict:
    return {name: entry["value"] for name, entry in (profile.fields or {}).items()}


def profile_view(profile: CompanyProfile) -> dict:
    return {
        "workspace_id": str(profile.workspace_id),
        "status": profile.status,
        "version": profile.version,
        "fields": profile_values(profile),
        "field_details": profile.fields or {},
        "missing": missing_fields(profile.fields or {}, COMPANY_FIELDS),
        "research": profile.research,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }


def complete_profile(session: Session, workspace_id: uuid.UUID) -> CompanyProfile:
    profile = get_profile(session, workspace_id, lock=True)
    missing = missing_fields(profile.fields or {}, COMPANY_FIELDS)
    if missing:
        raise Invalid("Required company fields are missing.", missing=missing)
    profile.status = "complete"
    session.flush()
    return profile
