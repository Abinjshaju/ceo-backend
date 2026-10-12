import hashlib
import io
import math
import os
import re
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field
from sqlalchemy import text, update

from ceo_backend.app import create_app
from ceo_backend.auth import mint_dev_token
from ceo_backend.config import Settings
from ceo_backend.db import Base
from ceo_backend.models import EMBEDDING_DIMENSIONS, ProcessingJob
from ceo_backend.services.container import build_services
from ceo_backend.services.jobs import drain

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class ScriptedChat(BaseChatModel):
    """A chat model that replays scripted replies. Items may be AIMessages or callables
    that receive the prompt messages. Structured outputs are scripted per schema."""

    responses: list[Any] = Field(default_factory=list)
    structured: dict[str, list[Any]] = Field(default_factory=dict)
    calls: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(list(messages))
        if not self.responses:
            raise AssertionError("ScriptedChat ran out of responses")
        item = self.responses.pop(0)
        if callable(item):
            item = item(messages)
        return ChatResult(generations=[ChatGeneration(message=item)])

    def with_structured_output(self, schema, **kwargs):
        def run(prompt):
            queue = self.structured.get(schema.__name__) or []
            if not queue:
                raise AssertionError(f"No scripted output for {schema.__name__}")
            item = queue.pop(0)
            if callable(item):
                item = item(prompt)
            return schema.model_validate(item) if isinstance(item, dict) else item

        return RunnableLambda(run)


class HashEmbedder:
    """Deterministic bag-of-words embeddings so semantic search is testable offline."""

    model_name = "hash-embedder"

    def _vector(self, text_value: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for token in re.findall(r"[a-z0-9]+", text_value.lower()):
            bucket = int(hashlib.md5(token.encode()).hexdigest(), 16) % EMBEDDING_DIMENSIONS
            vector[bucket] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text_value: str) -> list[float]:
        return self._vector(text_value)


class FakeProvider:
    model_name = "scripted-model"

    def __init__(self) -> None:
        self.model = ScriptedChat()
        self.embeddings = HashEmbedder()
        self.embeddings_enabled = True
        self.spoken: list[str] = []
        self.speech_error: Exception | None = None
        self.transcripts: list[str] = []
        self.heard: list[dict] = []

    def chat(self, purpose: str) -> ScriptedChat:
        return self.model

    def embedder(self):
        return self.embeddings if self.embeddings_enabled else None

    def speak(self, text_value: str):
        # Streamed like Gemini: errors surface on the first chunk. The second chunk is WAV-wrapped,
        # which some models send, so header removal is exercised.
        if self.speech_error:
            raise self.speech_error
        self.spoken.append(text_value)
        yield b"\x01\x00" * 1200
        yield b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 20 + b"data\x00\x00\x00\x00" + b"\x02\x00" * 1200

    def transcribe(self, audio: bytes, language: str | None, names: list[str]) -> str:
        if self.speech_error:
            raise self.speech_error
        self.heard.append({"bytes": len(audio), "language": language, "names": names})
        return self.transcripts.pop(0) if self.transcripts else ""

    # Script helpers
    def reply(self, *items) -> None:
        self.model.responses.extend(items)

    def structured(self, schema_name: str, *items) -> None:
        self.model.structured.setdefault(schema_name, []).extend(items)


def ai(text_value: str = "", *calls: dict) -> AIMessage:
    return AIMessage(
        content=text_value,
        tool_calls=[{"name": c["name"], "args": c.get("args", {}), "id": c.get("id") or uuid.uuid4().hex[:8]} for c in calls],
    )


def call(tool_name: str, /, **args) -> dict:
    return {"name": tool_name, "args": args}


def _reset_postgres(engine) -> None:
    # The suite drops the schema; refuse anything that isn't clearly a test database.
    if not engine.url.database.endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must point at a database whose name ends in _test.")
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


_pg_ready = False


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL or f"sqlite:///{tmp_path / 'test.db'}",
        gemini_api_key="test-key",
        auth_mode="hs256",
        auth_jwt_secret="test-secret-with-enough-length-for-hs256",
        storage_dir=str(tmp_path / "storage"),
        storage_signing_secret="storage-secret",
        public_base_url="http://testserver",
        frontend_dist=str(tmp_path / "no-frontend"),
        jobs_inline=False,
        research_enabled=False,
    )


@pytest.fixture
def services(settings, provider):
    global _pg_ready
    svc = build_services(settings, llm=provider)
    if TEST_DATABASE_URL:
        if not _pg_ready:
            _reset_postgres(svc.engine)
            _pg_ready = True
        tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
        with svc.engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield svc
    svc.engine.dispose()


@pytest.fixture
def client(services) -> TestClient:
    return TestClient(create_app(services), base_url="http://testserver/api", raise_server_exceptions=False)


@pytest.fixture
def token(settings) -> Callable[[str], str]:
    def make(email: str = "ceo@example.com") -> str:
        return mint_dev_token(settings, subject=f"user-{email}", email=email)

    return make


@pytest.fixture
def auth(token) -> Callable[[str], dict]:
    def headers(email: str = "ceo@example.com") -> dict:
        return {"Authorization": f"Bearer {token(email)}"}

    return headers


@pytest.fixture
def run_jobs(services) -> Callable[[], int]:
    return lambda: drain(services)


COMPLETE_COMPANY = {
    "name": "Acme Foods",
    "employee_count": "about 120",
    "products_services": "Grocery delivery",
    "main_customers": "Urban households",
    "industry": "Retail",
    "stage": "growth",
    "revenue_model": "Subscriptions and delivery fees",
    "markets": "India",
    "priorities": ["growth", "retention"],
}

COMPLETE_PROJECT = {
    "name": "Retention drive",
    "description": "Loyalty program for repeat customers",
    "objective": "Retention",
    "investment": "₹20 lakh",
    "financial_impact": "₹60 lakh extra revenue per year",
    "growth_outcomes": "+15% repeat customers",
    "owner": "Priya",
    "target_date": "December 2026",
    "risks": "Engineering capacity",
}


@pytest.fixture
def workspace_id(client, auth) -> str:
    return client.get("/me", headers=auth()).json()["workspaces"][0]["id"]


@pytest.fixture
def company_ready(client, auth, workspace_id, provider) -> str:
    """A workspace whose company profile is complete."""
    session = client.post(f"/workspaces/{workspace_id}/onboarding-sessions", json={"kind": "company"}, headers=auth()).json()
    provider.reply(
        ai("", call("save_company_fields", **COMPLETE_COMPANY)),
        ai("", call("finalize_company_profile")),
        ai("All set. Next, your first project."),
    )
    response = client.post(
        f"/onboarding-sessions/{session['id']}/messages",
        json={"client_message_id": "c-setup", "content": "Here is everything about Acme Foods. Confirmed."},
        headers=auth(),
    )
    assert response.status_code == 200, response.text
    return workspace_id


@pytest.fixture
def project_id(client, auth, company_ready, provider, services) -> str:
    """An active project with all required facts."""
    session = client.post(f"/workspaces/{company_ready}/onboarding-sessions", json={"kind": "project"}, headers=auth()).json()
    provider.reply(
        ai("", call("save_project_fields", **COMPLETE_PROJECT, documents="none")),
        ai("", call("finalize_project")),
        ai("Project created."),
    )
    response = client.post(
        f"/onboarding-sessions/{session['id']}/messages",
        json={"client_message_id": "p-setup", "content": "Retention drive details, no documents. Confirmed."},
        headers=auth(),
    )
    assert response.status_code == 200, response.text
    # Start each test with an empty queue so scripted outputs go to the jobs under test.
    with services.session() as db:
        db.execute(update(ProcessingJob).values(status="completed"))
        db.commit()
    return session["project_id"]


def make_pdf(pages: list[str]) -> bytes:
    """A minimal PDF with one text block per page."""
    objects: list[bytes] = []
    page_ids = [3 + i * 2 for i in range(len(pages))]
    font_id = 3 + len(pages) * 2
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for i, page_text in enumerate(pages):
        content_id = page_ids[i] + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        lines = []
        for line in page_text.split("\n"):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append(f"({escaped}) Tj T*")
        stream = ("BT /F1 12 Tf 14 TL 72 720 Td " + " ".join(lines) + " ET").encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def upload(client, auth_headers: dict, scope: str, filename: str, data: bytes) -> dict:
    """Run the signed upload flow: request a URL, PUT the bytes, complete."""
    target = client.post(f"{scope}/documents/upload-url",
                         json={"filename": filename, "size_bytes": len(data)}, headers=auth_headers)
    assert target.status_code == 200, target.text
    body = target.json()
    put = client.put(body["upload"]["url"], content=data, headers=body["upload"]["headers"])
    assert put.status_code == 204, put.text
    done = client.post(f"{scope}/documents/{body['document_id']}/complete",
                       json={"version_id": body["version_id"]}, headers=auth_headers)
    assert done.status_code == 200, done.text
    return body
