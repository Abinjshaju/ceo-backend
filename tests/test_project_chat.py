import json

from sqlalchemy import select

from ceo_backend.models import AgentRun, Artifact, ToolCall
from ceo_backend.services.projects import FactChange, apply_fact_changes
from tests.conftest import ai, call


def send(client, auth, project_id, content, client_message_id, **extra):
    return client.post(f"/projects/{project_id}/messages",
                       json={"client_message_id": client_message_id, "content": content, **extra}, headers=auth())


def test_explicit_correction_is_saved_with_history(client, auth, project_id, provider):
    provider.reply(
        ai("", call("update_project_facts", changes=[{"field": "investment", "value": "₹15 lakh"}])),
        ai("Saved: the budget is now ₹15 lakh [fact:investment]. Who is available to deliver by December?"),
    )
    response = send(client, auth, project_id, "We've reduced the budget to ₹15 lakh. Can we still make December?", "c1")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [c["ref"] for c in body["citations"]] == ["fact:investment"]

    facts = client.get(f"/projects/{project_id}/facts?history=true&field=investment", headers=auth()).json()
    assert facts["facts"]["investment"]["value"] == "₹15 lakh"
    assert facts["facts"]["investment"]["source"]["message_id"] is not None
    assert [h["value"] for h in facts["history"]] == ["₹20 lakh", "₹15 lakh"]
    assert facts["history"][1]["previous_value"] == "₹20 lakh"
    assert body["context"]["project_version"] == facts["project_version"]


def test_stale_write_is_rejected_and_reported_to_the_model(client, auth, project_id, provider, services):
    import uuid

    def concurrent_edit(_messages):
        with services.session() as db:
            apply_fact_changes(db, uuid.UUID(project_id), [FactChange("owner", "Arjun")],
                               expected_version=None, source_kind="form")
            db.commit()
        return ai("", call("update_project_facts", changes=[{"field": "owner", "value": "Meera"}]))

    provider.reply(concurrent_edit, ai("Someone else just changed the owner. Should it be Meera?"))
    send(client, auth, project_id, "Meera owns delivery now", "c1")
    facts = client.get(f"/projects/{project_id}/facts", headers=auth()).json()["facts"]
    assert facts["owner"]["value"] == "Arjun"
    with services.session() as db:
        failed = db.scalar(select(ToolCall).where(ToolCall.name == "update_project_facts"))
        assert failed.ok is False and "changed during this turn" in failed.result["error"]


def test_proposals_and_decisions_stay_distinct(client, auth, project_id, provider):
    provider.reply(
        ai("", call("update_project_facts", changes=[{"field": "team_capacity", "value": "2 more engineers", "status": "proposed"}]),
           call("record_decision", description="Keep the December launch", rationale="Board commitment"),
           call("manage_open_question", action="add", description="Is the second engineer budgeted?")),
        ai("Recorded the decision and a suggestion."),
    )
    send(client, auth, project_id, "We decided to keep the December launch for the board.", "c1")
    detail = client.get(f"/projects/{project_id}", headers=auth()).json()
    assert detail["facts"]["team_capacity"]["status"] == "proposed"
    assert detail["decisions"][0]["status"] == "adopted"
    question = detail["open_questions"][0]

    provider.reply(
        ai("", call("manage_open_question", action="resolve", question_id=question["id"], resolution="Yes, budgeted")),
        ai("Resolved."),
    )
    send(client, auth, project_id, "Yes, the second engineer is budgeted", "c2")
    resolved = client.get(f"/projects/{project_id}/open-questions?status=resolved", headers=auth()).json()
    assert resolved["open_questions"][0]["resolution"] == "Yes, budgeted"


def test_unsupported_citations_are_removed(client, auth, project_id, provider):
    provider.reply(ai("The owner is Priya [fact:owner]. The plan says March [doc:999] [msg:12345]."))
    body = send(client, auth, project_id, "Who owns it?", "c1").json()
    assert body["text"] == "The owner is Priya [fact:owner]. The plan says March."
    assert [c["ref"] for c in body["citations"]] == ["fact:owner"]


def test_components_are_validated_and_persisted(client, auth, project_id, provider, services):
    chart = {"chart_type": "bar", "unit": "₹ lakh",
             "series": [{"name": "Plan", "points": [{"x": "Investment", "y": 20}, {"x": "Revenue", "y": 60}]}]}
    provider.reply(
        ai("", call("present_component", component_type="chart", title="Plan", data_json=json.dumps(chart))),
        ai("", call("present_component", component_type="chart", title="Plan", data_json=json.dumps(chart),
                    source_refs=["fact:investment", "fact:financial_impact"])),
        ai("Here is the plan [fact:investment]."),
    )
    body = send(client, auth, project_id, "Chart investment against revenue", "c1").json()
    assert len(body["components"]) == 1
    component = body["components"][0]
    assert component["type"] == "chart" and component["source_refs"] == ["fact:investment", "fact:financial_impact"]
    with services.session() as db:
        calls = db.scalars(select(ToolCall).where(ToolCall.name == "present_component").order_by(ToolCall.id)).all()
        assert calls[0].ok is False and "must cite" in calls[0].result["error"]
        artifact = db.get(Artifact, __import__("uuid").UUID(component["artifact_id"]))
        assert artifact.message_id == body["message_id"]


def test_scenarios_explore_without_changing_facts_and_adopt_with_a_version(client, auth, project_id, provider):
    provider.reply(
        ai("", call("calculate_scenario", calculation_type="budget_change", investment=20, new_investment=15,
                    annual_benefit=60, years=1, currency="₹ lakh", title="Budget cut")),
        ai("ROI rises from 200% to 300% if benefit holds."),
    )
    body = send(client, auth, project_id, "What if we cut the budget to 15 lakh?", "c1").json()
    scenario = body["components"][0]
    assert scenario["type"] == "scenario"
    assert scenario["data"]["outputs"]["roi_percent_after"] == 300.0
    artifact_id = scenario["artifact_id"]
    version = body["context"]["project_version"]

    explored = client.post(f"/projects/{project_id}/artifacts/{artifact_id}/events",
                           json={"operation": "explore", "inputs": {"new_investment": 10}}, headers=auth()).json()
    assert explored["result"]["outputs"]["roi_percent_after"] == 500.0
    facts = client.get(f"/projects/{project_id}/facts", headers=auth()).json()
    assert facts["project_version"] == version and facts["facts"]["investment"]["value"] == "₹20 lakh"

    saved = client.post(f"/projects/{project_id}/artifacts/{artifact_id}/events",
                        json={"operation": "save_scenario", "inputs": {"new_investment": 10}, "name": "Lean"},
                        headers=auth()).json()
    assert saved["artifact"]["status"] == "saved" and saved["artifact"]["inputs"]["new_investment"] == 10

    stale = client.post(f"/projects/{project_id}/artifacts/{artifact_id}/events",
                        json={"operation": "adopt", "changes": [{"field": "investment", "value": "₹10 lakh"}],
                              "expected_version": version - 1}, headers=auth())
    assert stale.status_code == 409
    adopted = client.post(f"/projects/{project_id}/artifacts/{artifact_id}/events",
                          json={"operation": "adopt", "changes": [{"field": "investment", "value": "₹10 lakh"}],
                                "expected_version": version}, headers=auth())
    assert adopted.status_code == 200, adopted.text
    detail = client.get(f"/projects/{project_id}", headers=auth()).json()
    assert detail["facts"]["investment"]["value"] == "₹10 lakh"
    assert detail["facts"]["investment"]["source"]["kind"] == "scenario"
    assert any("Adopted scenario" in d["description"] for d in detail["decisions"])
    artifacts = client.get(f"/projects/{project_id}/artifacts", headers=auth()).json()["artifacts"]
    assert artifacts[0]["status"] == "adopted" and artifacts[0]["outdated"] is True


def test_form_component_saves_answers_as_facts(client, auth, project_id, provider):
    form = {"fields": [{"field": "team_capacity", "label": "Team size", "input_type": "number"}]}
    provider.reply(
        ai("", call("present_component", component_type="form", title="Missing details", data_json=json.dumps(form))),
        ai("Fill in the team size."),
    )
    body = send(client, auth, project_id, "What do you still need?", "c1").json()
    artifact_id = body["components"][0]["artifact_id"]
    version = body["context"]["project_version"]
    wrong = client.post(f"/projects/{project_id}/artifacts/{artifact_id}/events",
                        json={"operation": "submit_form", "changes": [{"field": "owner", "value": "X"}],
                              "expected_version": version}, headers=auth())
    assert wrong.status_code == 422
    ok = client.post(f"/projects/{project_id}/artifacts/{artifact_id}/events",
                     json={"operation": "submit_form", "changes": [{"field": "team_capacity", "value": "4"}],
                           "expected_version": version}, headers=auth())
    assert ok.status_code == 200
    facts = client.get(f"/projects/{project_id}/facts", headers=auth()).json()["facts"]
    assert facts["team_capacity"] == {**facts["team_capacity"], "value": "4", "status": "confirmed"}


def test_messages_are_idempotent_and_runs_are_recorded(client, auth, project_id, provider, services):
    provider.reply(ai("", call("get_project_context")), ai("The project is on track."))
    first = send(client, auth, project_id, "Status?", "same").json()
    second = send(client, auth, project_id, "Status?", "same").json()
    assert first["message_id"] == second["message_id"]
    assert not provider.model.responses
    run = client.get(f"/agent-runs/{first['run_id']}", headers=auth()).json()
    assert run["status"] == "completed"
    assert [c["name"] for c in run["tool_calls"]] == ["get_project_context"]
    assert client.get(f"/agent-runs/{first['run_id']}", headers=auth("other@example.com")).status_code == 404
    history = client.get(f"/projects/{project_id}/messages", headers=auth()).json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_failed_runs_are_marked_and_can_be_retried(client, auth, project_id, provider, services):
    def boom(_messages):
        raise RuntimeError("model unavailable")

    provider.reply(boom)
    response = send(client, auth, project_id, "Status?", "retry-me")
    assert response.status_code == 500
    with services.session() as db:
        assert db.scalar(select(AgentRun).where(AgentRun.kind == "project_chat")).status == "failed"
    provider.reply(ai("On track."))
    retried = send(client, auth, project_id, "Status?", "retry-me")
    assert retried.status_code == 200 and retried.json()["text"] == "On track."
    history = client.get(f"/projects/{project_id}/messages", headers=auth()).json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_streaming_emits_progress_text_and_completion(client, auth, project_id, provider):
    provider.reply(ai("", call("list_project_documents")), ai("No documents yet."))
    with client.stream("POST", f"/projects/{project_id}/messages?stream=true",
                       json={"client_message_id": "s1", "content": "Any documents?"}, headers=auth()) as response:
        assert response.status_code == 200
        raw = "".join(response.iter_text())
    events = [block.split("\n")[0].removeprefix("event: ") for block in raw.strip().split("\n\n")]
    assert events[0] == "run_started" and events[-1] == "completed"
    assert "progress" in events and "text_delta" in events
    completed = json.loads(raw.strip().split("\n\n")[-1].split("data: ", 1)[1])
    assert completed["text"] == "No documents yet."


def test_streaming_reports_errors_before_the_run(client, auth, project_id):
    with client.stream("POST", f"/projects/{project_id}/messages?stream=true",
                       json={"client_message_id": "s1", "content": "x", "document_ids": [
                           "00000000-0000-0000-0000-000000000000"]}, headers=auth()) as response:
        raw = "".join(response.iter_text())
    assert raw.startswith("event: failed") and '"status": 404' in raw


def test_memory_extraction_respects_confirmed_facts(client, auth, project_id, provider, run_jobs):
    provider.reply(ai("Noted."))
    body = send(client, auth, project_id, "Two engineers are free from November. If budget were 30 lakh we'd add QA. "
                                          "Owner is actually Rahul? not sure.", "c1").json()
    user_message_id = body["message_id"] - 1
    provider.structured("MemoryExtraction", {
        "facts": [
            {"field": "team_capacity", "value": "Two engineers from November", "status": "confirmed",
             "hypothetical": False, "source_message_id": user_message_id},
            {"field": "investment", "value": "₹30 lakh", "status": "confirmed", "hypothetical": True,
             "source_message_id": user_message_id},
            {"field": "owner", "value": "Rahul", "status": "confirmed", "hypothetical": False,
             "source_message_id": user_message_id},
            {"field": "risks", "value": "made up", "status": "confirmed", "hypothetical": False,
             "source_message_id": 999999},
        ],
        "decisions": [{"description": "Add QA", "adopted_by_user": False, "source_message_id": user_message_id}],
        "open_questions": [],
        "answered_questions": [],
    })
    provider.reply(ai("Summary: two engineers are available from November; owner may change."))
    assert run_jobs() == 2

    detail = client.get(f"/projects/{project_id}", headers=auth()).json()
    assert detail["facts"]["team_capacity"]["source"]["kind"] == "memory"
    assert detail["facts"]["investment"]["value"] == "₹20 lakh"
    assert detail["facts"]["owner"]["value"] == "Priya"
    assert detail["facts"]["risks"]["value"] == "Engineering capacity"
    assert any("Rahul" in q["description"] for q in detail["open_questions"])
    assert detail["decisions"][0]["status"] == "proposed"
    assert detail["summary"].startswith("Summary:")
    assert detail["latest_assessment"] is None or detail["latest_assessment"]["outdated"] in (True, False)


def test_archived_projects_leave_the_default_list(client, auth, project_id, company_ready):
    listing = client.get(f"/workspaces/{company_ready}/projects", headers=auth()).json()
    row = listing["projects"][0]
    assert row["name"] == "Retention drive" and row["investment"] == "₹20 lakh"
    assert row["feasibility"] is None and row["assessment_status"] == "none"
    version = client.get(f"/projects/{project_id}", headers=auth()).json()["version"]
    client.patch(f"/projects/{project_id}", json={"expected_version": version, "status": "archived"}, headers=auth())
    assert client.get(f"/workspaces/{company_ready}/projects", headers=auth()).json()["total"] == 0
    assert client.get(f"/workspaces/{company_ready}/projects?status=archived", headers=auth()).json()["total"] == 1
    searched = client.get(f"/workspaces/{company_ready}/projects?status=archived&search=retent", headers=auth()).json()
    assert searched["total"] == 1


def test_decisions_and_questions_are_citable(client, auth, project_id, provider):
    provider.reply(
        ai("", call("record_decision", description="Launch English-only first")),
        lambda messages: ai(f"Recorded {json.loads(messages[-1].content)['ref'].join('[]')}."),
    )
    body = send(client, auth, project_id, "We decided to launch English-only first.", "c1").json()
    assert [c["kind"] for c in body["citations"]] == ["decision"]
    decision_ref = body["citations"][0]["ref"]

    provider.reply(ai(f"Per [{decision_ref}] and [decision:99999], English ships first."))
    body = send(client, auth, project_id, "What did we decide about languages?", "c2").json()
    assert body["text"] == f"Per [{decision_ref}] and, English ships first."
    assert [c["ref"] for c in body["citations"]] == [decision_ref]


def test_deleting_a_project_removes_it_and_its_files(client, auth, project_id, company_ready, services, run_jobs):
    from tests.conftest import upload

    body = upload(client, auth(), f"/projects/{project_id}", "notes.txt", b"hello")
    run_jobs()
    assert client.delete(f"/projects/{project_id}", headers=auth("other@example.com")).status_code == 404
    assert client.delete(f"/projects/{project_id}", headers=auth()).status_code == 204
    assert client.get(f"/projects/{project_id}", headers=auth()).status_code == 404
    assert client.get(f"/workspaces/{company_ready}/projects", headers=auth()).json()["total"] == 0
    assert list((services.storage.root).rglob("*.txt")) == []
    assert body["document_id"]


def test_transient_model_failures_are_retried_once(client, auth, project_id, provider):
    def dropped(_messages):
        raise ConnectionError("Server disconnected without sending a response.")

    provider.reply(dropped, ai("Recovered."))
    body = send(client, auth, project_id, "Status?", "flaky").json()
    assert body["text"] == "Recovered."

    provider.reply(dropped, dropped)
    failed = send(client, auth, project_id, "Status again?", "flaky-2")
    assert failed.status_code == 500


def test_slow_model_calls_time_out(client, auth, project_id, provider, monkeypatch):
    import time as _time

    from ceo_backend.agents import runtime

    monkeypatch.setattr(runtime, "MODEL_CALL_TIMEOUT", 0.2)

    def slow(_messages):
        _time.sleep(1)
        return ai("too late")

    provider.reply(slow, slow)
    response = send(client, auth, project_id, "Status?", "slow")
    assert response.status_code == 504


def test_component_sources_survive_without_prose_citations(client, auth, project_id, provider):
    provider.reply(
        ai("", call("present_component", component_type="table", title="Investment",
                    data_json=json.dumps({"columns": ["Metric", "Value"], "rows": [["Investment", "₹20 lakh"]]}),
                    source_refs=["fact:investment"])),
        ai("Here is your comparison."),
    )
    response = send(client, auth, project_id, "Show the investment table", "artifact-sources").json()
    assert [c["ref"] for c in response["citations"]] == ["fact:investment"]
    component = response["components"][0]
    assert component["citations"][0]["field"] == "investment"
    stored = client.get(f"/projects/{project_id}/artifacts", headers=auth()).json()["artifacts"]
    assert stored[0]["citations"] == component["citations"]
