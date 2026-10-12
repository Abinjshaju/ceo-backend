import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from ceo_backend.api import auth, documents, projects, speech, workspace
from ceo_backend.config import Settings
from ceo_backend.errors import DomainError
from ceo_backend.services.container import Services, build_services
from ceo_backend.services.storage import API_PREFIX

log = logging.getLogger(__name__)


def create_app(services: Services | None = None) -> FastAPI:
    services = services or build_services(Settings.from_env())
    app = FastAPI(title="CEO Workspace API", version="1.0.0")
    app.state.services = services

    if services.settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=services.settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": type(exc).__name__, "message": exc.message, **exc.details}},
            headers={"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "Invalid", "message": "Invalid request.",
                               "fields": exc.errors()}},
        )

    @app.exception_handler(TimeoutError)
    async def timeout_error(_: Request, exc: TimeoutError) -> JSONResponse:
        return JSONResponse(status_code=504, content={"error": {"code": "Timeout", "message": str(exc)}})

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "Internal", "message": "Something went wrong. Please try again."}},
        )

    @app.get(f"{API_PREFIX}/health")
    @app.get("/healthz", include_in_schema=False)
    def health() -> dict:
        with services.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "model_configured": bool(services.settings.gemini_api_key),
            "model": services.settings.gemini_model,
            "auth_mode": services.settings.auth_mode,
        }

    for module in (auth, workspace, projects, documents, speech):
        app.include_router(module.router, prefix=API_PREFIX)

    # The built frontend, served from the same origin as the API.
    dist = Path(services.settings.frontend_dist)
    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
