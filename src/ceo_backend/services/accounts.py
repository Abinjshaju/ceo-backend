"""Email and password accounts for AUTH_MODE=local. Passwords are hashed with scrypt;
repeated failures lock the account briefly."""

import base64
import hashlib
import hmac
import os
import re
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ceo_backend.auth import LOCAL_ISSUER, Principal, get_or_create_user, mint_dev_token
from ceo_backend.config import Settings
from ceo_backend.db import utcnow
from ceo_backend.errors import Conflict, DomainError, Invalid, Unauthorized
from ceo_backend.models import LocalCredential, User

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
MAX_FAILED_ATTEMPTS = 10
LOCKOUT = timedelta(minutes=15)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Compared against when the email is unknown, so both paths take the same time.
_DUMMY_HASH: str | None = None


class TooManyAttempts(DomainError):
    status_code = 429


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, n, r, p, salt, digest = encoded.split("$")
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, base64.b64decode(digest))


def _normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL.match(email) or len(email) > 320:
        raise Invalid("Enter a valid email address.")
    return email


def _check_password(password: str) -> None:
    if len(password) < 8:
        raise Invalid("Use a password of at least 8 characters.")
    if len(password) > 256:
        raise Invalid("That password is too long.")


def issue_token(settings: Settings, user: User) -> dict:
    ttl = settings.auth_token_ttl_hours * 3600
    token = mint_dev_token(settings, user.auth_subject, user.email, user.name, ttl)
    return {
        "token": token,
        "expires_in": ttl,
        "user": {"id": str(user.id), "email": user.email, "name": user.name},
    }


def sign_up(session: Session, settings: Settings, email: str, password: str, name: str | None) -> dict:
    email = _normalize_email(email)
    _check_password(password)
    if session.scalar(select(LocalCredential).where(LocalCredential.email == email)):
        raise Conflict("An account with this email already exists. Sign in instead.")
    name = (name or "").strip()[:255] or email.split("@")[0]
    user = get_or_create_user(session, Principal(LOCAL_ISSUER, str(uuid.uuid4()), email, name))
    session.add(LocalCredential(user_id=user.id, email=email, password_hash=hash_password(password)))
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict("An account with this email already exists. Sign in instead.") from exc
    return issue_token(settings, user)


def sign_in(session: Session, settings: Settings, email: str, password: str) -> dict:
    global _DUMMY_HASH
    email = email.strip().lower()
    credential = session.scalar(
        select(LocalCredential).where(LocalCredential.email == email).with_for_update()
    )
    if credential is None:
        _DUMMY_HASH = _DUMMY_HASH or hash_password("unused-password")
        verify_password(password, _DUMMY_HASH)
        raise Unauthorized("Email or password is incorrect.")
    if credential.locked_until and credential.locked_until > utcnow():
        raise TooManyAttempts("Too many attempts. Try again in a few minutes.")
    if not verify_password(password, credential.password_hash):
        credential.failed_attempts += 1
        if credential.failed_attempts >= MAX_FAILED_ATTEMPTS:
            credential.failed_attempts = 0
            credential.locked_until = utcnow() + LOCKOUT
        session.commit()
        raise Unauthorized("Email or password is incorrect.")
    credential.failed_attempts = 0
    credential.locked_until = None
    session.commit()
    return issue_token(settings, session.get(User, credential.user_id))
