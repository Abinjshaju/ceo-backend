import time
import uuid
from dataclasses import dataclass

import jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ceo_backend.config import Settings
from ceo_backend.errors import NotFound, Unauthorized
from ceo_backend.models import Project, User, Workspace, WorkspaceMember


LOCAL_ISSUER = "ceo-backend"


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    email: str | None
    name: str | None


class TokenVerifier:
    """Verifies bearer tokens from an OIDC provider (JWKS) or a shared HS256 secret."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._jwks = (
            jwt.PyJWKClient(settings.auth_jwks_url, cache_keys=True, lifespan=3600)
            if settings.auth_mode == "jwks" and settings.auth_jwks_url
            else None
        )

    def verify(self, token: str) -> Principal:
        s = self.settings
        options: dict = {"require": ["exp", "sub"]}
        kwargs: dict = {}
        if s.auth_audience:
            kwargs["audience"] = s.auth_audience
        else:
            options["verify_aud"] = False
        if s.auth_mode == "local":
            kwargs["issuer"] = LOCAL_ISSUER
        elif s.auth_issuer:
            kwargs["issuer"] = s.auth_issuer

        try:
            if s.auth_mode in ("hs256", "local"):
                if not s.auth_jwt_secret:
                    raise Unauthorized("Authentication is not configured on the server.")
                claims = jwt.decode(
                    token, s.auth_jwt_secret, algorithms=["HS256"], options=options, leeway=30, **kwargs
                )
            elif s.auth_mode == "jwks":
                if self._jwks is None:
                    raise Unauthorized("Authentication is not configured on the server.")
                key = self._jwks.get_signing_key_from_jwt(token).key
                claims = jwt.decode(
                    token, key, algorithms=["RS256", "ES256", "EdDSA"], options=options, leeway=30, **kwargs
                )
            else:
                raise Unauthorized(f"Unsupported AUTH_MODE {s.auth_mode!r}.")
        except jwt.PyJWTError as exc:
            raise Unauthorized("Invalid or expired token.") from exc

        return Principal(
            issuer=str(claims.get("iss") or s.auth_issuer or "local"),
            subject=str(claims["sub"]),
            email=claims.get("email"),
            name=claims.get("name"),
        )


def mint_dev_token(settings: Settings, subject: str, email: str | None = None, name: str | None = None,
                   ttl_seconds: int = 86_400) -> str:
    """Issue an HS256 token. Used for local sign-in, development, and tests."""
    if settings.auth_mode not in ("hs256", "local") or not settings.auth_jwt_secret:
        raise RuntimeError("Tokens can only be issued with AUTH_MODE=local or hs256 and AUTH_JWT_SECRET set.")
    now = int(time.time())
    claims = {"sub": subject, "iat": now, "exp": now + ttl_seconds}
    if email:
        claims["email"] = email
    if name:
        claims["name"] = name
    if settings.auth_mode == "local":
        claims["iss"] = LOCAL_ISSUER
    elif settings.auth_issuer:
        claims["iss"] = settings.auth_issuer
    if settings.auth_audience:
        claims["aud"] = settings.auth_audience
    return jwt.encode(claims, settings.auth_jwt_secret, algorithm="HS256")


def get_or_create_user(session: Session, principal: Principal) -> User:
    """Find the user for a verified principal. First sign-in creates the user with a
    personal workspace where they are the owner."""
    query = select(User).where(
        User.auth_issuer == principal.issuer, User.auth_subject == principal.subject
    )
    user = session.scalar(query)
    if user is not None:
        if principal.email and user.email != principal.email:
            user.email = principal.email
            session.commit()
        return user

    user = User(
        id=uuid.uuid4(),
        auth_issuer=principal.issuer,
        auth_subject=principal.subject,
        email=principal.email,
        name=principal.name,
    )
    workspace = Workspace(id=uuid.uuid4(), name=f"{principal.name or principal.email or 'My'} workspace")
    session.add_all([user, workspace])
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
    try:
        session.commit()
    except IntegrityError:
        # A concurrent first request created the user; use that one.
        session.rollback()
        user = session.scalar(query)
        if user is None:
            raise
    return user


def require_membership(session: Session, user: User, workspace_id: uuid.UUID) -> WorkspaceMember:
    member = session.get(WorkspaceMember, (workspace_id, user.id))
    if member is None:
        raise NotFound("Workspace not found.")
    return member


def require_project(session: Session, user: User, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None or session.get(WorkspaceMember, (project.workspace_id, user.id)) is None:
        raise NotFound("Project not found.")
    return project
