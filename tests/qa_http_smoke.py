"""HTTP checks for the disposable fixture: python -m tests.qa_http_smoke.

Start tests.qa_browser_server first. Never point this mutating check at production.
"""
import json
import re
import uuid
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8787"


def main():
    run_id = uuid.uuid4().hex
    checks = []
    token = None

    def request(path, method="GET", body=None, expected=200, authenticated=True, raw=False):
        headers = {}
        if token and authenticated:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + path, method=method, headers=headers,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            response = urllib.request.urlopen(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        assert response.status == expected, (path, response.status, response.read().decode())
        payload = response.read()
        checks.append(f"{method} {path}: {expected}")
        if raw or not payload:
            return payload
        assert response.headers["Content-Type"].startswith("application/json")
        return json.loads(payload)

    assert request("/api/health")["status"] == "ok"
    homepage = request("/", raw=True).decode()
    for path in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', homepage):
        assert request(path, raw=True)
    request("/api/me", authenticated=False, expected=401)
    request("/api/auth/login", "POST", {"email": "qa@example.invalid", "password": "incorrect-password"}, expected=401)
    token = request("/api/auth/login", "POST", {"email": "qa@example.invalid", "password": "QA-test-password-2026"})["token"]
    me = request("/api/me")
    assert me["user"]["email"] == "qa@example.invalid"
    workspace = me["workspaces"][0]["id"]
    assert request(f"/api/workspaces/{workspace}/company")["status"] == "complete"
    project = request(f"/api/workspaces/{workspace}/projects")["projects"][0]
    pid = project["id"]
    assert request(f"/api/projects/{pid}")["name"] == "Retention drive"
    for view in ("table", "chart", "scenario"):
        path = f"/api/projects/{pid}/messages?stream=true"
        body = {"client_message_id": f"qa-{run_id}-{view}", "content": f"Show a {view}"}
        stream = request(path, "POST", body, raw=True).decode()
        events = [(block.split('event: ')[1].splitlines()[0], json.loads(block.split('data: ', 1)[1]))
                  for block in stream.split('\n\n') if 'event: ' in block and 'data: ' in block]
        completed = next(payload for event, payload in events if event == "completed")
        assert completed["components"][0]["type"] == view
        assert any(event == "component_ready" for event, _ in events)
        assert not any(event == "failed" for event, _ in events)
        if view != "scenario":
            assert completed["components"][0]["citations"]
        # Repeating a client message returns the same durable response.
        again = request(f"/api/projects/{pid}/messages", "POST", body)
        assert again["message_id"] == completed["message_id"]
    artifacts = request(f"/api/projects/{pid}/artifacts")["artifacts"]
    assert {item["type"] for item in artifacts} == {"table", "chart", "scenario"}
    assert all(item["citations"] for item in artifacts if item["type"] != "scenario")
    scenario = next(item for item in artifacts if item["type"] == "scenario")
    result = request(f"/api/projects/{pid}/artifacts/{scenario['artifact_id']}/events", "POST",
                     {"operation": "explore", "inputs": {"investment": 30}})
    assert result["operation"] == "explore"
    detail = request(f"/api/projects/{pid}")
    request(f"/api/projects/{pid}", "PATCH", {"expected_version": detail["version"], "status": "archived"})
    assert request(f"/api/workspaces/{workspace}/projects")["total"] == 0
    detail = request(f"/api/projects/{pid}")
    request(f"/api/projects/{pid}", "PATCH", {"expected_version": detail["version"], "status": "active"})
    assert request(f"/api/workspaces/{workspace}/projects")["total"] == 1
    # A second temporary fixture user must not access the first workspace or artifacts.
    outsider = request("/api/auth/signup", "POST", {"email": f"outsider-{run_id}@example.invalid", "password": "QA-test-password-2026"})
    token = outsider["token"]
    request(f"/api/projects/{pid}", expected=404)
    request(f"/api/projects/{pid}/artifacts", expected=404)
    print(json.dumps({"passed": True, "checks": len(checks), "results": checks}, indent=2))


if __name__ == "__main__":
    main()
