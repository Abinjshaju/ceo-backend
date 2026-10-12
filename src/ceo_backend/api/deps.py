import json
import logging
import queue
import threading
from collections.abc import Callable, Iterator

from fastapi import BackgroundTasks, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ceo_backend.agents.runtime import Emit
from ceo_backend.auth import get_or_create_user
from ceo_backend.errors import DomainError, Unauthorized
from ceo_backend.models import User
from ceo_backend.services.container import Services
from ceo_backend.services.jobs import drain
from ceo_backend.services.turns import user_error

log = logging.getLogger(__name__)


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_session(services: Services = Depends(get_services)) -> Iterator[Session]:
    session = services.session()
    try:
        yield session
    finally:
        session.close()


def current_user(request: Request, session: Session = Depends(get_session),
                 services: Services = Depends(get_services)) -> User:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized("Missing bearer token.")
    principal = services.verifier.verify(token.strip())
    return get_or_create_user(session, principal)


def schedule_jobs(background: BackgroundTasks, services: Services) -> None:
    if services.settings.jobs_inline:
        background.add_task(drain, services)


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def stream_turn(services: Services, turn: Callable[[Session, Emit], dict]) -> StreamingResponse:
    """Run a turn on a worker thread with its own session and stream its events.
    The turn finishes and is saved even if the client disconnects."""
    events: queue.Queue = queue.Queue()
    sent: set[str] = set()

    def emit(event: str, data: dict) -> None:
        sent.add(event)
        events.put(sse(event, data))

    def worker() -> None:
        try:
            with services.session() as session:
                turn(session, emit)
        except Exception as exc:  # noqa: BLE001
            if "failed" not in sent and "completed" not in sent:
                status = exc.status_code if isinstance(exc, DomainError) else 500
                details = exc.details if isinstance(exc, DomainError) else {}
                emit("failed", {"error": user_error(exc), "status": status, **details})
        finally:
            events.put(None)
            if services.settings.jobs_inline:
                try:
                    drain(services)
                except Exception:  # noqa: BLE001
                    log.exception("inline job drain failed")

    threading.Thread(target=worker, daemon=True).start()

    def body() -> Iterator[str]:
        while (item := events.get()) is not None:
            yield item

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
