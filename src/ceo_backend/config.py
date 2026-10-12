import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./ceo_dev.db"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.6-flash"
    embedding_model: str = "gemini-embedding-001"
    embeddings_enabled: bool = True
    research_enabled: bool = True
    ocr_enabled: bool = True
    # Spoken replies. Without speech (or a key) the browser reads replies with its own voice.
    speech_enabled: bool = True
    speech_model: str = "gemini-3.8-flash-lite-tts"
    speech_voice: str = "Kore"
    # Voice conversation: what the user says is transcribed by Gemini.
    transcription_enabled: bool = True
    transcription_model: str = "gemini-3.6-flash"

    # "local": this server keeps email/password accounts and issues its own tokens.
    # "jwks": tokens come from an OIDC provider. "hs256": tokens signed with a shared secret.
    auth_mode: str = "local"
    auth_token_ttl_hours: int = 168
    auth_jwks_url: str | None = None
    auth_issuer: str | None = None
    auth_audience: str | None = None
    auth_jwt_secret: str | None = None

    cors_origins: list[str] = field(default_factory=list)
    # Origin used in signed upload links. Empty makes them relative to the page, which suits
    # a single-origin deployment.
    public_base_url: str = ""
    frontend_dist: str = "frontend/dist"

    storage_backend: str = "local"
    storage_dir: str = "./storage"
    storage_signing_secret: str | None = None
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_endpoint_url: str | None = None
    upload_url_ttl_seconds: int = 600
    max_upload_bytes: int = 25 * 1024 * 1024

    jobs_inline: bool = True

    agent_max_rounds: int = 8
    agent_time_limit_seconds: int = 120
    context_char_budget: int = 24_000

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            gemini_api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", cls.gemini_model),
            embedding_model=os.getenv("EMBEDDING_MODEL", cls.embedding_model),
            embeddings_enabled=_bool("EMBEDDINGS_ENABLED", True),
            research_enabled=_bool("RESEARCH_ENABLED", True),
            ocr_enabled=_bool("OCR_ENABLED", True),
            speech_enabled=_bool("SPEECH_ENABLED", True),
            speech_model=os.getenv("SPEECH_MODEL", cls.speech_model),
            speech_voice=os.getenv("SPEECH_VOICE", cls.speech_voice),
            transcription_enabled=_bool("TRANSCRIPTION_ENABLED", True),
            transcription_model=os.getenv("TRANSCRIPTION_MODEL", cls.transcription_model),
            auth_mode=os.getenv("AUTH_MODE", cls.auth_mode),
            auth_jwks_url=os.getenv("AUTH_JWKS_URL"),
            auth_issuer=os.getenv("AUTH_ISSUER"),
            auth_audience=os.getenv("AUTH_AUDIENCE"),
            auth_jwt_secret=os.getenv("AUTH_JWT_SECRET"),
            auth_token_ttl_hours=int(os.getenv("AUTH_TOKEN_TTL_HOURS", cls.auth_token_ttl_hours)),
            cors_origins=_list("CORS_ORIGINS"),
            public_base_url=os.getenv("PUBLIC_BASE_URL", cls.public_base_url).rstrip("/"),
            frontend_dist=os.getenv("FRONTEND_DIST", cls.frontend_dist),
            storage_backend=os.getenv("STORAGE_BACKEND", cls.storage_backend),
            storage_dir=os.getenv("STORAGE_DIR", cls.storage_dir),
            storage_signing_secret=os.getenv("STORAGE_SIGNING_SECRET"),
            s3_bucket=os.getenv("S3_BUCKET"),
            s3_region=os.getenv("S3_REGION"),
            s3_endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            upload_url_ttl_seconds=int(os.getenv("UPLOAD_URL_TTL_SECONDS", cls.upload_url_ttl_seconds)),
            max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", cls.max_upload_bytes)),
            jobs_inline=_bool("JOBS_INLINE", True),
        )
