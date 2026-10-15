"""Market research report catalog offered during project intake.

Frost & Sullivan is the only provider for now. Each report is either actual, meaning
it has a file that can be imported into the project, or a sample listing. Sample
listings appear in the project's document list as "listed": they are never ingested
or searched, so the assistant cannot quote them as evidence."""

import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ceo_backend.models import Document, DocumentVersion, Project
from ceo_backend.services import jobs
from ceo_backend.services.extraction import SUPPORTED_TYPES

LISTED_STATUS = "listed"
REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Report:
    id: str
    provider: str
    title: str
    year: int
    topics: tuple[str, ...]
    file: Path | None = None  # set only for actual reports
    summary: str = ""
    pages: int | None = None
    cover: str | None = None  # served from frontend/public; None means a generated cover

    @property
    def actual(self) -> bool:
        return self.file is not None


REPORTS = (
    Report(
        id="frost-top-50-2024", provider="Frost & Sullivan", title="Top 50 Emerging Technologies: 2024 Edition",
        year=2024, topics=("top 50", "emerging technologies", "technology", "innovation", "ranking", "growth"),
        file=REPO_ROOT / "demo" / "2024 Top 50 Actual Report.pdf",
        summary=("Frost & Sullivan's ranking of the 50 most valuable and disruptive emerging technologies, "
                 "drawn from over 3,000 technologies assessed across 9 areas. Explains how each is expected "
                 "to change organizations over the next 2 to 4 years and where to focus technology roadmaps."),
        pages=636, cover="/report-covers/frost-top-50-2024.jpg",
    ),
    Report("frost-digital-2025", "Frost & Sullivan", "Sample: digital transformation outlook", 2025,
           ("digital", "transformation", "software", "saas", "technology", "automation")),
    Report("frost-fintech-2025", "Frost & Sullivan", "Sample: fintech and payments market", 2025,
           ("fintech", "payments", "banking", "financial", "finance")),
    Report("frost-supply-2025", "Frost & Sullivan", "Sample: supply chain and logistics", 2025,
           ("supply chain", "logistics", "manufacturing", "operations", "distribution")),
    Report("frost-healthcare-2025", "Frost & Sullivan", "Sample: healthcare technology", 2025,
           ("healthcare", "health", "hospital", "pharma", "medical")),
)

_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def search_reports(query: str, limit: int = 6) -> list[Report]:
    """Rank reports by how many query words appear in their topics, provider, or title.
    Actual reports win ties so the real file is shown first."""
    query_words = _words(query)
    lowered = query.lower()
    scored = []
    for report in REPORTS:
        haystack = _words(f"{report.provider} {report.title} {' '.join(report.topics)}")
        phrase_hits = sum(1 for topic in report.topics if topic in lowered)
        score = len(query_words & haystack) + 2 * phrase_hits
        if score:
            scored.append((score, report))
    scored.sort(key=lambda item: (-item[0], not item[1].actual))
    return [report for _, report in scored[:limit]]


SAMPLE_SUMMARY = "Sample listing for reference only. No report file is attached, so nothing from it is used."


def report_view(report: Report) -> dict:
    return {
        "id": report.id,
        "provider": report.provider,
        "title": report.title,
        "year": report.year,
        "actual": report.actual,
        "summary": report.summary or SAMPLE_SUMMARY,
        "pages": report.pages,
        "cover": report.cover,
    }


def catalog(session: Session, project: Project) -> list[dict]:
    """Every report offered, marked added when this project already has its document."""
    names = set(session.scalars(select(Document.filename).where(
        Document.project_id == project.id, Document.status != "deleted",
    )))
    return [{**report_view(r), "added": (r.file.name if r.file else f"{r.title}.pdf") in names} for r in REPORTS]


def _existing(session: Session, project: Project, filename: str) -> Document | None:
    return session.scalar(select(Document).where(
        Document.project_id == project.id, Document.filename == filename, Document.status != "deleted",
    ))


def import_reports(services, session: Session, project: Project, user_id: uuid.UUID | None,
                   report_ids: list[str]) -> dict:
    """Add the chosen reports to the project. Actual reports are imported from their file and
    ingested like any upload; sample listings are recorded as listed only."""
    by_id = {report.id: report for report in REPORTS}
    added, skipped = [], []
    for report_id in report_ids:
        report = by_id.get(report_id)
        if report is None:
            skipped.append({"id": report_id, "reason": "Unknown report."})
            continue
        if report.actual:
            outcome = _import_actual(services, session, project, user_id, report)
        else:
            outcome = _list_sample(session, project, user_id, report)
        if outcome is None:
            added.append({"id": report.id, "title": report.title})
        else:
            skipped.append({"id": report.id, "title": report.title, "reason": outcome})
    session.commit()
    return {"added": added, "skipped": skipped}


def _list_sample(session: Session, project: Project, user_id, report: Report) -> str | None:
    filename = f"{report.title}.pdf"
    if _existing(session, project, filename) is not None:
        return "Already in this project."
    session.add(Document(
        id=uuid.uuid4(), workspace_id=project.workspace_id, project_id=project.id, filename=filename,
        content_type=SUPPORTED_TYPES[".pdf"], status=LISTED_STATUS, created_by=user_id,
    ))
    return None


def _import_actual(services, session: Session, project: Project, user_id, report: Report) -> str | None:
    if _existing(session, project, report.file.name) is not None:
        return "Already in this project."
    if not report.file.is_file():
        return "The report file is not available on this server."
    size = report.file.stat().st_size
    if size > services.settings.max_upload_bytes:
        limit_mb = services.settings.max_upload_bytes // (1024 * 1024)
        return f"The file is larger than the {limit_mb} MB upload limit."

    data = report.file.read_bytes()
    document = Document(
        id=uuid.uuid4(), workspace_id=project.workspace_id, project_id=project.id, filename=report.file.name,
        content_type=SUPPORTED_TYPES[".pdf"], status="processing", created_by=user_id,
    )
    version = DocumentVersion(
        id=uuid.uuid4(), document_id=document.id, version=1, filename=report.file.name,
        content_type=SUPPORTED_TYPES[".pdf"], declared_size=size, size_bytes=size,
        sha256=hashlib.sha256(data).hexdigest(), status="processing",
        storage_key=f"{project.workspace_id}/{document.id}/{uuid.uuid4().hex}.pdf",
    )
    services.storage.write(version.storage_key, data)
    session.add_all([document, version])
    session.flush()
    jobs.enqueue(session, "ingest_document", {"version_id": str(version.id)}, f"ingest:{version.id}")
    return None
