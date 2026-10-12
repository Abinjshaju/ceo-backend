import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ceo_backend.db import Base, JSONType, UTCDateTime, Vector, utcnow

EMBEDDING_DIMENSIONS = 768


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(UTCDateTime, default=utcnow, nullable=False)


# Identity and access


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("auth_issuer", "auth_subject"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    auth_issuer: Mapped[str] = mapped_column(String(512))
    auth_subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created()


class LocalCredential(Base):
    """Email and password sign-in for AUTH_MODE=local."""

    __tablename__ = "local_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = _created()


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created()


class WorkspaceMember(Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), default="owner")
    created_at: Mapped[datetime] = _created()


# Company and onboarding


class CompanyProfile(Base):
    __tablename__ = "company_profiles"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    # {field: {"value", "status", "source", "source_message_id", "updated_at"}}
    fields: Mapped[dict] = mapped_column(JSONType, default=dict)
    research: Mapped[dict | None] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # company_onboarding | project_onboarding | project_chat
    kind: Mapped[str] = mapped_column(String(30))
    memory_through_message_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class OnboardingSession(Base):
    __tablename__ = "onboarding_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))  # company | project
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | completed
    # Intake answers that are not facts, e.g. {"documents": "none" | "attached"}.
    state: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("conversation_id", "client_message_id", "role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    client_message_id: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    document_ids: Mapped[list] = mapped_column(JSONType, default=list)
    citations: Mapped[list] = mapped_column(JSONType, default=list)
    components: Mapped[list] = mapped_column(JSONType, default=list)
    suggested_actions: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = _created()


# Projects and knowledge


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255), default="Untitled project")
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | active | archived
    version: Mapped[int] = mapped_column(Integer, default=0)
    finalize_key: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ProjectFact(Base):
    __tablename__ = "project_facts"
    __table_args__ = (UniqueConstraint("project_id", "field"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(50))
    value: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    source_section_id: Mapped[int | None] = mapped_column(ForeignKey("document_sections.id", ondelete="SET NULL"))
    source_kind: Mapped[str] = mapped_column(String(20), default="chat")  # chat | form | document | memory | scenario
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    effective_date: Mapped[str | None] = mapped_column(String(40))
    project_version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class FactVersion(Base):
    __tablename__ = "fact_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    field: Mapped[str] = mapped_column(String(50))
    previous_value: Mapped[str | None] = mapped_column(Text)
    value: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    source_section_id: Mapped[int | None] = mapped_column(ForeignKey("document_sections.id", ondelete="SET NULL"))
    source_kind: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    project_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="adopted")  # adopted | proposed
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    source_kind: Mapped[str] = mapped_column(String(20), default="chat")
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()


class OpenQuestion(Base):
    __tablename__ = "open_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | resolved
    resolution: Mapped[str | None] = mapped_column(Text)
    source_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    resolution_message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    source_kind: Mapped[str] = mapped_column(String(20), default="chat")
    created_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ProjectSummary(Base):
    __tablename__ = "project_summaries"

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    through_message_id: Mapped[int | None] = mapped_column(Integer)
    project_version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Assessment(Base):
    __tablename__ = "assessments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="decision_brief")
    content: Mapped[dict] = mapped_column(JSONType)
    project_version: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()


# Documents


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    # Null project_id means a company-level document shared across projects.
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(150))
    # uploading | processing | ready | failed | deleted
    status: Mapped[str] = mapped_column(String(20), default="uploading")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created()
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(150))
    storage_key: Mapped[str] = mapped_column(String(512))
    declared_size: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    # uploading | processing | ready | failed | superseded
    status: Mapped[str] = mapped_column(String(20), default="uploading")
    extraction_status: Mapped[str] = mapped_column(String(20), default="pending")
    index_status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    extractor_version: Mapped[str | None] = mapped_column(String(20))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DocumentSection(Base):
    __tablename__ = "document_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    sheet: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))


# Interactive artifacts


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSONType)
    inputs: Mapped[dict | None] = mapped_column(JSONType)
    calculation_version: Mapped[str | None] = mapped_column(String(20))
    source_refs: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="presented")  # presented | saved | adopted
    saved_name: Mapped[str | None] = mapped_column(String(255))
    project_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()


class ArtifactEvent(Base):
    __tablename__ = "artifact_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    operation: Mapped[str] = mapped_column(String(30))  # explore | save_scenario | adopt | submit_form
    inputs: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = _created()


# Execution and jobs


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(30))
    # queued | running | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="running")
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    user_message_id: Mapped[int | None] = mapped_column(Integer)
    assistant_message_id: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict | list | None] = mapped_column(JSONType)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_processing_jobs_ready", "status", "run_after"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
