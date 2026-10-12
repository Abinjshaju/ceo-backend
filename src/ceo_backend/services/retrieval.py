"""Hybrid retrieval over project documents and conversation history.

Postgres uses full-text search and pgvector; SQLite falls back to in-process scoring.
Keyword and vector rankings are combined with reciprocal rank fusion. Every query is
filtered to the caller's workspace and project before anything reaches the model."""

import logging
import math
import re
import uuid
from collections import Counter

from sqlalchemy import Float, literal_column, or_, select
from sqlalchemy import func as sql
from sqlalchemy.orm import Session

from ceo_backend.db import is_postgres
from ceo_backend.errors import NotFound
from ceo_backend.models import Document, DocumentSection, DocumentVersion, Message

log = logging.getLogger(__name__)

STOPWORDS = set(
    "a an and are as at be but by can could did do does for from had has have how i if in into is it its "
    "me my of on or our so than that the their them then there these they this to us was we were what "
    "when where which who why will with would you your".split()
)
ENGLISH = literal_column("'english'::regconfig")
SNIPPET_CHARS = 700
RRF_K = 60


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9.%-]*", text.lower()) if t not in STOPWORDS]


def _or_query(text: str) -> str:
    words = [re.sub(r"[^a-z0-9]", "", t) for t in tokens(text)]
    return " OR ".join(dict.fromkeys(w for w in words if w))


def section_tsvector():
    return sql.to_tsvector(ENGLISH, sql.coalesce(DocumentSection.heading, "") + " " + DocumentSection.text)


def message_tsvector():
    return sql.to_tsvector(ENGLISH, Message.content)


def _bm25(query_tokens: list[str], docs: list[tuple[int, str]]) -> list[int]:
    if not query_tokens or not docs:
        return []
    tokenized = [(doc_id, tokens(text)) for doc_id, text in docs]
    avg_len = sum(len(t) for _, t in tokenized) / len(tokenized) or 1
    df = Counter(term for _, terms in tokenized for term in set(terms))
    n = len(tokenized)
    scores = []
    for doc_id, terms in tokenized:
        counts = Counter(terms)
        score = 0.0
        for term in set(query_tokens):
            if term not in counts:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            tf = counts[term]
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(terms) / avg_len))
        if score > 0:
            scores.append((score, doc_id))
    return [doc_id for _, doc_id in sorted(scores, reverse=True)]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def _section_scope(workspace_id: uuid.UUID, project_id: uuid.UUID | None, document_id: uuid.UUID | None):
    conditions = [
        DocumentSection.workspace_id == workspace_id,
        Document.deleted_at.is_(None),
        Document.current_version_id == DocumentSection.document_version_id,
        # A project sees its own documents plus company-level shared documents.
        or_(DocumentSection.project_id == project_id, DocumentSection.project_id.is_(None))
        if project_id else DocumentSection.project_id.is_(None),
    ]
    if document_id:
        conditions.append(DocumentSection.document_id == document_id)
    return conditions


def _snippet(text: str, query_tokens: list[str]) -> str:
    lower = text.lower()
    positions = [lower.find(t) for t in query_tokens if lower.find(t) >= 0]
    start = max(0, min(positions) - 150) if positions else 0
    snippet = text[start : start + SNIPPET_CHARS].strip()
    return ("…" if start else "") + snippet + ("…" if start + SNIPPET_CHARS < len(text) else "")


def section_ref(section: DocumentSection) -> str:
    return f"doc:{section.id}"


def section_citation(section: DocumentSection, document: Document, version: DocumentVersion | None) -> dict:
    return {
        "ref": section_ref(section),
        "kind": "document",
        "document_id": str(document.id),
        "filename": version.filename if version else document.filename,
        "version": version.version if version else None,
        "section_id": section.id,
        "page": section.page,
        "heading": section.heading,
        "sheet": section.sheet,
    }


def search_documents(services, session: Session, workspace_id: uuid.UUID, project_id: uuid.UUID | None,
                     query: str, limit: int = 6, document_id: uuid.UUID | None = None) -> list[dict]:
    scope = _section_scope(workspace_id, project_id, document_id)
    base = select(DocumentSection.id).join(Document, Document.id == DocumentSection.document_id).where(*scope)
    query_tokens = tokens(query)

    keyword: list[int] = []
    vector: list[int] = []
    if is_postgres(session):
        or_query = _or_query(query)
        if or_query:
            tsq = sql.websearch_to_tsquery(ENGLISH, or_query)
            keyword = list(session.scalars(
                base.where(section_tsvector().op("@@")(tsq))
                .order_by(sql.ts_rank_cd(section_tsvector(), tsq).desc())
                .limit(30)
            ))
    else:
        rows = session.execute(
            select(DocumentSection.id, DocumentSection.heading, DocumentSection.text)
            .join(Document, Document.id == DocumentSection.document_id).where(*scope).limit(5000)
        ).all()
        keyword = _bm25(query_tokens, [(r.id, f"{r.heading or ''} {r.text}") for r in rows])[:30]

    embedder = services.llm.embedder()
    if embedder is not None:
        try:
            query_vector = embedder.embed_query(query)
            if is_postgres(session):
                distance = DocumentSection.embedding.op("<=>", return_type=Float)(query_vector)
                vector = list(session.scalars(
                    base.where(DocumentSection.embedding.is_not(None)).order_by(distance).limit(30)
                ))
            else:
                rows = session.execute(
                    select(DocumentSection.id, DocumentSection.embedding)
                    .join(Document, Document.id == DocumentSection.document_id)
                    .where(*scope, DocumentSection.embedding.is_not(None)).limit(5000)
                ).all()
                scored = sorted(((_cosine(query_vector, r.embedding), r.id) for r in rows), reverse=True)
                vector = [section_id for score, section_id in scored[:30] if score > 0]
        except Exception as exc:  # noqa: BLE001
            log.warning("vector search unavailable: %s", exc)

    fused = rrf([keyword, vector])[:limit]
    if not fused:
        return []
    ids = [section_id for section_id, _ in fused]
    rows = session.execute(
        select(DocumentSection, Document, DocumentVersion)
        .join(Document, Document.id == DocumentSection.document_id)
        .join(DocumentVersion, DocumentVersion.id == DocumentSection.document_version_id)
        .where(DocumentSection.id.in_(ids))
    ).all()
    by_id = {row[0].id: row for row in rows}
    results = []
    for section_id, score in fused:
        section, document, version = by_id[section_id]
        results.append({
            **section_citation(section, document, version),
            "score": round(score, 4),
            "matched_by": [name for name, ranking in (("keyword", keyword), ("semantic", vector)) if section_id in ranking],
            "snippet": _snippet(section.text, query_tokens),
        })
    return results


def read_section(session: Session, workspace_id: uuid.UUID, project_id: uuid.UUID | None, section_id: int,
                 neighbors: int = 1, max_chars: int = 9000) -> list[dict]:
    row = session.execute(
        select(DocumentSection, Document)
        .join(Document, Document.id == DocumentSection.document_id)
        .where(DocumentSection.id == section_id, *_section_scope(workspace_id, project_id, None))
    ).first()
    if row is None:
        raise NotFound(f"Section {section_id} not found in this project's documents.")
    section, document = row
    neighbors = max(0, min(neighbors, 3))
    sections = session.scalars(
        select(DocumentSection)
        .where(
            DocumentSection.document_version_id == section.document_version_id,
            DocumentSection.ordinal.between(section.ordinal - neighbors, section.ordinal + neighbors),
        )
        .order_by(DocumentSection.ordinal)
    ).all()
    version = session.get(DocumentVersion, section.document_version_id)
    results, used = [], 0
    for item in sections:
        text = item.text[: max(0, max_chars - used)]
        used += len(text)
        results.append({**section_citation(item, document, version), "text": text, "requested": item.id == section_id})
    return results


def search_messages(session: Session, conversation_ids: list[uuid.UUID], query: str, limit: int = 8,
                    exclude_ids: set[int] | None = None) -> list[dict]:
    if not conversation_ids:
        return []
    exclude_ids = exclude_ids or set()
    base = select(Message).where(Message.conversation_id.in_(conversation_ids))
    if is_postgres(session):
        or_query = _or_query(query)
        if not or_query:
            return []
        tsq = sql.websearch_to_tsquery(ENGLISH, or_query)
        found = session.scalars(
            base.where(message_tsvector().op("@@")(tsq))
            .order_by(sql.ts_rank_cd(message_tsvector(), tsq).desc())
            .limit(limit + len(exclude_ids))
        ).all()
    else:
        candidates = session.scalars(base.order_by(Message.id.desc()).limit(3000)).all()
        ranked = _bm25(tokens(query), [(m.id, m.content) for m in candidates])
        by_id = {m.id: m for m in candidates}
        found = [by_id[i] for i in ranked[: limit + len(exclude_ids)]]
    query_tokens = tokens(query)
    return [
        {
            "ref": f"msg:{m.id}",
            "kind": "message",
            "message_id": m.id,
            "role": m.role,
            "created_at": m.created_at.isoformat(),
            "snippet": _snippet(m.content, query_tokens),
        }
        for m in found
        if m.id not in exclude_ids
    ][:limit]
