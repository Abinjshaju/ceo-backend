"""Database-backed job queue for ingestion, memory, summaries, and briefs.

Jobs are deduplicated by key, claimed atomically, retried with backoff, and safe to
run from several workers at once."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ceo_backend.db import utcnow
from ceo_backend.models import ProcessingJob

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)


@dataclass
class Handler:
    run: Callable  # (services, session, payload) -> None
    on_failure: Callable | None = None  # (services, session, payload, error) -> None


HANDLERS: dict[str, Handler] = {}


def register(kind: str, on_failure: Callable | None = None):
    def decorator(fn: Callable) -> Callable:
        HANDLERS[kind] = Handler(fn, on_failure)
        return fn

    return decorator


def enqueue(session: Session, kind: str, payload: dict, dedupe_key: str, max_attempts: int = 3) -> None:
    """Add a job unless one with the same key exists. Commits nothing; the caller's
    transaction decides."""
    if session.scalar(select(ProcessingJob.id).where(ProcessingJob.dedupe_key == dedupe_key)):
        return
    try:
        with session.begin_nested():
            session.add(
                ProcessingJob(kind=kind, payload=payload, dedupe_key=dedupe_key, max_attempts=max_attempts)
            )
    except IntegrityError:
        pass


def requeue(session: Session, dedupe_key: str) -> bool:
    job = session.scalar(select(ProcessingJob).where(ProcessingJob.dedupe_key == dedupe_key))
    if job is None or job.status not in ("failed", "completed"):
        return False
    job.status, job.attempts, job.last_error, job.run_after = "queued", 0, None, utcnow()
    return True


def _claim(session: Session) -> ProcessingJob | None:
    now = utcnow()
    # Recover jobs whose worker died mid-run.
    session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.status == "running", ProcessingJob.locked_at < now - STALE_AFTER)
        .values(status="queued")
    )
    session.commit()
    candidates = session.scalars(
        select(ProcessingJob.id)
        .where(ProcessingJob.status == "queued", ProcessingJob.run_after <= now)
        .order_by(ProcessingJob.id)
        .limit(5)
    ).all()
    for job_id in candidates:
        claimed = session.execute(
            update(ProcessingJob)
            .where(ProcessingJob.id == job_id, ProcessingJob.status == "queued")
            .values(status="running", locked_at=now, attempts=ProcessingJob.attempts + 1)
        )
        session.commit()
        if claimed.rowcount == 1:
            return session.get(ProcessingJob, job_id)
    return None


def run_one(services) -> bool:
    """Claim and run a single ready job. Returns False when nothing was ready."""
    import ceo_backend.services.handlers  # noqa: F401  (registers handlers)

    with services.session() as session:
        job = _claim(session)
        if job is None:
            return False
        handler = HANDLERS.get(job.kind)
        job_id, kind, payload, attempts, max_attempts = job.id, job.kind, dict(job.payload), job.attempts, job.max_attempts

    started = time.monotonic()
    error: Exception | None = None
    with services.session() as session:
        try:
            if handler is None:
                raise RuntimeError(f"No handler for job kind {kind!r}")
            handler.run(services, session, payload)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            error = exc
            log.exception("job %s (%s) failed on attempt %s", job_id, kind, attempts)

    with services.session() as session:
        job = session.get(ProcessingJob, job_id)
        if error is None:
            job.status, job.last_error = "completed", None
        elif attempts < max_attempts:
            job.status, job.last_error = "queued", str(error)[:2000]
            job.run_after = utcnow() + timedelta(seconds=5 * 2 ** (attempts - 1))
        else:
            job.status, job.last_error = "failed", str(error)[:2000]
            if handler and handler.on_failure:
                try:
                    handler.on_failure(services, session, payload, error)
                except Exception:  # noqa: BLE001
                    log.exception("failure hook for job %s raised", job_id)
        session.commit()
    log.info("job %s (%s) %s in %.2fs", job_id, kind, "ok" if error is None else "error", time.monotonic() - started)
    return True


def drain(services, max_jobs: int = 100) -> int:
    """Run ready jobs until none are left. Used for inline mode and tests."""
    count = 0
    while count < max_jobs and run_one(services):
        count += 1
    return count


def worker_loop(services, poll_seconds: float = 1.0, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    log.info("worker started")
    while not stop.is_set():
        try:
            if not run_one(services):
                stop.wait(poll_seconds)
        except Exception:  # noqa: BLE001
            log.exception("worker loop error")
            stop.wait(poll_seconds)
