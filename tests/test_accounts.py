import dataclasses

import pytest
from fastapi.testclient import TestClient

from ceo_backend.app import create_app
from ceo_backend.services.container import build_services


@pytest.fixture
def local_client(settings, provider, tmp_path):
    local = dataclasses.replace(settings, auth_mode="local", database_url=f"sqlite:///{tmp_path / 'local.db'}")
    services = build_services(local, llm=provider)
    yield TestClient(create_app(services), base_url="http://testserver/api", raise_server_exceptions=False)
    services.engine.dispose()


def test_sign_up_then_sign_in(local_client):
    created = local_client.post("/auth/signup", json={"email": " CEO@Example.com ", "password": "correct horse", "name": "Asha"})
    assert created.status_code == 200, created.text
    token = created.json()["token"]
    me = local_client.get("/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["user"] == {**me["user"], "email": "ceo@example.com", "name": "Asha"}
    assert len(me["workspaces"]) == 1

    signed_in = local_client.post("/auth/login", json={"email": "ceo@example.com", "password": "correct horse"})
    assert signed_in.status_code == 200
    again = local_client.get("/me", headers={"Authorization": f"Bearer {signed_in.json()['token']}"}).json()
    assert again["workspaces"][0]["id"] == me["workspaces"][0]["id"]


def test_sign_up_validation_and_duplicates(local_client):
    assert local_client.post("/auth/signup", json={"email": "nope", "password": "longenough"}).status_code == 422
    assert local_client.post("/auth/signup", json={"email": "a@b.co", "password": "short"}).status_code == 422
    assert local_client.post("/auth/signup", json={"email": "a@b.co", "password": "longenough"}).status_code == 200
    assert local_client.post("/auth/signup", json={"email": "A@B.co", "password": "longenough"}).status_code == 409


def test_wrong_passwords_lock_the_account(local_client):
    local_client.post("/auth/signup", json={"email": "a@b.co", "password": "longenough"})
    assert local_client.post("/auth/login", json={"email": "nobody@b.co", "password": "x"}).status_code == 401
    for _ in range(10):
        assert local_client.post("/auth/login", json={"email": "a@b.co", "password": "wrong-pass"}).status_code == 401
    locked = local_client.post("/auth/login", json={"email": "a@b.co", "password": "longenough"})
    assert locked.status_code == 429


def test_tokens_from_other_issuers_are_rejected(local_client, settings):
    from ceo_backend.auth import mint_dev_token

    foreign = mint_dev_token(dataclasses.replace(settings, auth_mode="hs256", auth_issuer="someone-else"), "x")
    assert local_client.get("/me", headers={"Authorization": f"Bearer {foreign}"}).status_code == 401


def test_password_sign_in_is_off_in_other_modes(client):
    assert client.get("/auth/config").json()["password_sign_in"] is False
    assert client.post("/auth/login", json={"email": "a@b.co", "password": "longenough"}).status_code == 404


def test_frontend_is_served_when_built(settings, provider, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>app</html>")
    services = build_services(dataclasses.replace(settings, frontend_dist=str(dist)), llm=provider)
    client = TestClient(create_app(services))
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/healthz").status_code == 200
    services.engine.dispose()
