from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ceo_backend.api.deps import get_services, get_session
from ceo_backend.errors import NotFound
from ceo_backend.services.accounts import sign_in, sign_up
from ceo_backend.services.container import Services

router = APIRouter(prefix="/auth")


class SignUpIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)
    name: str | None = Field(default=None, max_length=255)


class SignInIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


def _require_local(services: Services) -> None:
    if services.settings.auth_mode != "local":
        raise NotFound("Password sign-in is not enabled on this server.")


@router.get("/config")
def auth_config(services: Services = Depends(get_services)) -> dict:
    return {"mode": services.settings.auth_mode, "password_sign_in": services.settings.auth_mode == "local"}


@router.post("/signup")
def signup(body: SignUpIn, session: Session = Depends(get_session), services: Services = Depends(get_services)) -> dict:
    _require_local(services)
    return sign_up(session, services.settings, body.email, body.password, body.name)


@router.post("/login")
def login(body: SignInIn, session: Session = Depends(get_session), services: Services = Depends(get_services)) -> dict:
    _require_local(services)
    return sign_in(session, services.settings, body.email, body.password)
