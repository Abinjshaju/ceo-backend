"""Disposable browser QA server with scripted AI. Run: python -m tests.qa_browser_server.

Creates its database/storage in a temporary directory. No production data or paid API
is used. Sign in at localhost:8787 with qa@example.invalid / QA-test-password-2026.
"""
import tempfile
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage, ToolMessage
from sqlalchemy import update

from ceo_backend.app import create_app
from ceo_backend.config import Settings
from ceo_backend.models import ProcessingJob
from ceo_backend.services.container import build_services
from tests.conftest import COMPLETE_COMPANY, COMPLETE_PROJECT, FakeProvider, ai, call


def main():
    with tempfile.TemporaryDirectory(prefix="ceo-browser-qa-") as directory:
        provider = FakeProvider()
        services = build_services(Settings(database_url=f"sqlite:///{directory}/qa.db", auth_mode="local",
            auth_jwt_secret="qa-only-secret-with-sufficient-length", storage_signing_secret="qa-storage-secret",
            storage_dir=f"{directory}/storage", frontend_dist=str(Path("frontend/dist").resolve()),
            jobs_inline=False, research_enabled=False, gemini_api_key=None), llm=provider)
        app = create_app(services)
        client = TestClient(app, base_url="http://testserver/api")
        signed = client.post("/auth/signup", json={"email": "qa@example.invalid", "password": "QA-test-password-2026", "name": "QA Tester"}).json()
        headers = {"Authorization": f"Bearer {signed['token']}"}
        workspace = client.get("/me", headers=headers).json()["workspaces"][0]["id"]
        profile = client.patch(f"/workspaces/{workspace}/company", headers=headers,
            json={"expected_version": 0, "fields": COMPLETE_COMPANY, "complete": True})
        assert profile.status_code == 200, profile.text
        onboarding = client.post(f"/workspaces/{workspace}/onboarding-sessions", headers=headers, json={"kind": "project"}).json()
        provider.reply(ai("", call("save_project_fields", **COMPLETE_PROJECT, documents="none")),
            ai("", call("finalize_project")), ai("Project created."))
        result = client.post(f"/onboarding-sessions/{onboarding['id']}/messages", headers=headers,
            json={"client_message_id": "qa-seed", "content": "All project details provided. Confirmed."})
        assert result.status_code == 200, result.text
        with services.session() as session:
            session.execute(update(ProcessingJob).values(status="completed"))
            session.commit()

        def answer(messages):
            import json
            if isinstance(messages[-1], ToolMessage):
                return ai("Here is the requested view. Investment is ₹20 lakh [fact:investment].")
            human = next((message for message in reversed(messages) if isinstance(message, HumanMessage)), None)
            text = str(human.content).lower() if human else ""
            if "chart" in text or "graph" in text:
                data = {"chart_type": "bar", "unit": "₹ lakh", "series": [{"name": "Plan", "points": [{"x": "Investment", "y": 20}, {"x": "Revenue", "y": 60}]}]}
                return ai("", call("present_component", component_type="chart", title="Investment and revenue", data_json=json.dumps(data), source_refs=["fact:investment", "fact:financial_impact"]))
            if "table" in text:
                return ai("", call("present_component", component_type="table", title="Investment table", data_json=json.dumps({"columns": ["Metric", "Amount"], "rows": [["Investment", "₹20 lakh [fact:investment]"]]}), source_refs=["fact:investment"]))
            if "scenario" in text:
                return ai("", call("calculate_scenario", calculation_type="roi", investment=20, annual_benefit=60, years=1, currency="₹ lakh"))
            return ai("The project investment is ₹20 lakh [fact:investment]. Delivery owner is Priya [fact:owner].")

        provider.reply(*([answer] * 200))
        print("QA fixture ready: http://127.0.0.1:8787", flush=True)
        try:
            uvicorn.run(app, host="127.0.0.1", port=8787, log_level="warning")
        finally:
            services.engine.dispose()


if __name__ == "__main__":
    main()
