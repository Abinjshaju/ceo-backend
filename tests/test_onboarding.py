from sqlalchemy import select

from ceo_backend.models import Assessment, ToolCall
from tests.conftest import COMPLETE_COMPANY, COMPLETE_PROJECT, ai, call


def start(client, auth, workspace_id, kind):
    response = client.post(f"/workspaces/{workspace_id}/onboarding-sessions", json={"kind": kind}, headers=auth())
    assert response.status_code == 200, response.text
    return response.json()


def send(client, auth, session_id, content, client_message_id):
    return client.post(f"/onboarding-sessions/{session_id}/messages",
                       json={"client_message_id": client_message_id, "content": content}, headers=auth())


def test_one_answer_fills_several_company_fields(client, auth, workspace_id, provider):
    session = start(client, auth, workspace_id, "company")
    provider.reply(
        ai("", call("save_company_fields", name="Acme Foods", industry="Retail", stage="growth",
                    products_services="Grocery delivery"),
           call("suggest_answers", options=["Under 50", "50-200", "200+"])),
        ai("Thanks! Roughly how many people work at Acme Foods?"),
    )
    response = send(client, auth, session["id"], "Acme Foods, a growth-stage retail grocery delivery company", "m1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "1"
    assert body["text"].startswith("Thanks!")
    assert [a["label"] for a in body["suggested_actions"]] == ["Under 50", "50-200", "200+"]
    fields = body["onboarding"]["intake"]["fields"]
    assert fields["name"] == "Acme Foods" and fields["stage"] == "growth"
    assert "employee_count" in body["onboarding"]["intake"]["missing"]

    company = client.get(f"/workspaces/{workspace_id}/company", headers=auth()).json()
    assert company["field_details"]["name"]["source_message_id"] is not None


def test_unknown_values_count_only_where_allowed(client, auth, workspace_id, provider):
    session = start(client, auth, workspace_id, "company")
    provider.reply(
        ai("", call("save_company_fields", unknown_fields=["employee_count"])),
        ai("No problem."),
    )
    body = send(client, auth, session["id"], "Not sure how many employees", "m1").json()
    assert body["onboarding"]["intake"]["fields"]["employee_count"] == "unknown"
    assert "employee_count" not in body["onboarding"]["intake"]["missing"]


def test_finalize_is_refused_until_complete(client, auth, workspace_id, provider, services):
    session = start(client, auth, workspace_id, "company")
    provider.reply(ai("", call("finalize_company_profile")), ai("We still need a few details."))
    send(client, auth, session["id"], "Done, finish it", "m1")
    with services.session() as db:
        result = db.scalar(select(ToolCall).where(ToolCall.name == "finalize_company_profile"))
        assert result.ok is False
        assert "name" in result.result["missing"]
    assert client.get(f"/workspaces/{workspace_id}/company", headers=auth()).json()["status"] == "draft"


def test_company_onboarding_completes_and_cannot_restart(client, auth, company_ready):
    company = client.get(f"/workspaces/{company_ready}/company", headers=auth()).json()
    assert company["status"] == "complete"
    assert company["fields"]["priorities"] == ["growth", "retention"]
    response = client.post(f"/workspaces/{company_ready}/onboarding-sessions", json={"kind": "company"}, headers=auth())
    assert response.status_code == 409


def test_research_runs_once_and_is_kept_separate(client, auth, workspace_id, provider, services):
    object.__setattr__(services.settings, "research_enabled", True)
    session = start(client, auth, workspace_id, "company")
    provider.reply(
        ai("", call("save_company_fields", name="Acme Foods", industry="Retail")),
        ai('{"employee_count": "about 150", "markets": "India, UAE", "sources": ["https://acme.example"]}'),
        ai("", call("save_company_fields", stage="growth")),
        ai("Found online: about 150 employees. Is that right?"),
    )
    send(client, auth, session["id"], "Acme Foods, retail", "m1")
    company = client.get(f"/workspaces/{workspace_id}/company", headers=auth()).json()
    assert company["research"]["findings"] == {"employee_count": "about 150", "markets": "India, UAE"}
    assert company["research"]["sources"] == ["https://acme.example"]
    assert "employee_count" not in company["fields"]
    assert not provider.model.responses  # the second save did not search again


def test_onboarding_resumes_the_active_session(client, auth, workspace_id, provider):
    first = start(client, auth, workspace_id, "company")
    provider.reply(ai("", call("save_company_fields", name="Acme Foods")), ai("Which industry?"))
    send(client, auth, first["id"], "Acme Foods", "m1")
    again = start(client, auth, workspace_id, "company")
    assert again["id"] == first["id"]
    assert [m["role"] for m in again["messages"]] == ["user", "assistant"]
    me = client.get("/me", headers=auth()).json()
    assert me["workspaces"][0]["active_onboarding"][0]["id"] == first["id"]


def test_repeated_message_returns_the_stored_reply(client, auth, workspace_id, provider):
    session = start(client, auth, workspace_id, "company")
    provider.reply(ai("Hello!"))
    first = send(client, auth, session["id"], "Hi", "same-id").json()
    second = send(client, auth, session["id"], "Hi", "same-id").json()
    assert first["message_id"] == second["message_id"]
    conflict = send(client, auth, session["id"], "Something else", "same-id")
    assert conflict.status_code == 409


def test_project_onboarding_requires_a_complete_company(client, auth, workspace_id):
    response = client.post(f"/workspaces/{workspace_id}/onboarding-sessions", json={"kind": "project"}, headers=auth())
    assert response.status_code == 409


def test_project_onboarding_creates_an_active_project(client, auth, company_ready, provider, run_jobs, services):
    session = start(client, auth, company_ready, "project")
    assert session["intake"]["project_status"] == "draft"
    provider.reply(
        ai("", call("save_project_fields", **{k: v for k, v in COMPLETE_PROJECT.items() if k != "risks"})),
        ai("", call("finalize_project")),
        ai("What are the main risks, and do you have supporting documents?"),
    )
    body = send(client, auth, session["id"], "Retention drive …", "p1").json()
    intake = body["onboarding"]["intake"]
    assert intake["missing"] == ["risks"]
    assert intake["project_status"] == "draft"

    provider.reply(
        ai("", call("save_project_fields", unknown_fields=["risks"], documents="none")),
        ai("", call("finalize_project")),
        ai("Created. Preparing your decision brief."),
    )
    body = send(client, auth, session["id"], "Risks unknown, no documents. Confirmed.", "p2").json()
    assert body["onboarding"]["status"] == "completed"
    assert body["onboarding"]["intake"]["project_status"] == "active"

    project_id = session["project_id"]
    again = client.post(f"/projects/{project_id}/finalize", json={"idempotency_key": "k1"}, headers=auth()).json()
    assert again["already_finalized"] is True

    provider.structured("DecisionBrief", {
        "score": 62, "feasibility": "Moderate", "confidence": "Medium", "investment": "₹20 lakh",
        "expected_impact": "₹60 lakh extra revenue per year", "growth": "+15% repeat customers", "roi": None,
        "target_date": "December 2026", "summary": "A loyalty program.", "key_risks": [],
        "open_questions": ["Risks are unknown"], "assumptions": [], "recommended_next_step": "Size the team.",
    })
    assert run_jobs() >= 1
    with services.session() as db:
        assert db.scalar(select(Assessment)) is not None
    detail = client.get(f"/projects/{project_id}", headers=auth()).json()
    assert detail["latest_assessment"]["content"]["score"] == 62
    assert detail["latest_assessment"]["outdated"] is False
    assert detail["facts"]["risks"]["value"] == "unknown"
    assert detail["facts"]["owner"]["source"]["kind"] == "chat"


def test_company_profile_form_edit_uses_version_check(client, auth, company_ready):
    company = client.get(f"/workspaces/{company_ready}/company", headers=auth()).json()
    ok = client.patch(f"/workspaces/{company_ready}/company",
                      json={"expected_version": company["version"], "fields": {"markets": "India, UAE"}}, headers=auth())
    assert ok.status_code == 200 and ok.json()["fields"]["markets"] == "India, UAE"
    stale = client.patch(f"/workspaces/{company_ready}/company",
                         json={"expected_version": company["version"], "fields": {"markets": "UK"}}, headers=auth())
    assert stale.status_code == 409
    bad = client.patch(f"/workspaces/{company_ready}/company",
                       json={"expected_version": ok.json()["version"], "fields": {"stage": "huge"}}, headers=auth())
    assert bad.status_code == 422


def test_complete_company_fixture_matches_fields():
    from ceo_backend.fields import COMPANY_FIELDS

    assert set(COMPLETE_COMPANY) == set(COMPANY_FIELDS)


def test_profile_form_can_finish_company_onboarding(client, auth, workspace_id):
    session = start(client, auth, workspace_id, "company")
    fields = {k: v for k, v in COMPLETE_COMPANY.items()}
    incomplete = client.patch(f"/workspaces/{workspace_id}/company",
                              json={"expected_version": 0, "fields": {"name": "Acme"}, "complete": True}, headers=auth())
    assert incomplete.status_code == 422
    done = client.patch(f"/workspaces/{workspace_id}/company",
                        json={"expected_version": 0, "fields": fields, "complete": True}, headers=auth())
    assert done.status_code == 200 and done.json()["status"] == "complete"
    assert client.get(f"/onboarding-sessions/{session['id']}", headers=auth()).json()["status"] == "completed"
