def test_requests_without_a_token_are_rejected(client):
    response = client.get("/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "Unauthorized"


def test_invalid_token_is_rejected(client):
    response = client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401


def test_first_sign_in_creates_user_and_workspace(client, auth):
    first = client.get("/me", headers=auth()).json()
    second = client.get("/me", headers=auth()).json()
    assert first["user"]["email"] == "ceo@example.com"
    assert len(first["workspaces"]) == 1
    assert first["workspaces"][0]["role"] == "owner"
    assert first["workspaces"][0]["company_status"] == "draft"
    assert second["workspaces"][0]["id"] == first["workspaces"][0]["id"]


def test_users_cannot_reach_other_workspaces(client, auth, workspace_id):
    other = auth("intruder@example.com")
    assert client.get(f"/workspaces/{workspace_id}/company", headers=other).status_code == 404
    assert client.get(f"/workspaces/{workspace_id}/projects", headers=other).status_code == 404
    assert client.post(f"/workspaces/{workspace_id}/onboarding-sessions", json={"kind": "company"},
                       headers=other).status_code == 404


def test_users_cannot_reach_other_projects(client, auth, project_id):
    other = auth("intruder@example.com")
    for path in ("", "/messages", "/facts", "/decisions", "/open-questions", "/documents", "/artifacts"):
        assert client.get(f"/projects/{project_id}{path}", headers=other).status_code == 404, path
    response = client.post(f"/projects/{project_id}/messages",
                           json={"client_message_id": "x", "content": "hi"}, headers=other)
    assert response.status_code == 404
