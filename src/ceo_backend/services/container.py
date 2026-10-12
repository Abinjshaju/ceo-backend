from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ceo_backend.auth import TokenVerifier
from ceo_backend.config import Settings
from ceo_backend.db import Base, make_engine, make_sessionmaker
from ceo_backend.llm import GeminiProvider, LLMProvider
from ceo_backend.services.storage import Storage, make_storage


@dataclass
class Services:
    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    llm: LLMProvider
    storage: Storage
    verifier: TokenVerifier

    def session(self) -> Session:
        return self.sessions()


def build_services(
    settings: Settings,
    llm: LLMProvider | None = None,
    storage: Storage | None = None,
) -> Services:
    engine = make_engine(settings.database_url)
    if engine.dialect.name == "sqlite":
        # SQLite is for tests and quick local runs; Postgres uses Alembic migrations.
        import ceo_backend.models  # noqa: F401

        Base.metadata.create_all(engine)
    return Services(
        settings=settings,
        engine=engine,
        sessions=make_sessionmaker(engine),
        llm=llm or GeminiProvider(settings),
        storage=storage or make_storage(settings),
        verifier=TokenVerifier(settings),
    )
