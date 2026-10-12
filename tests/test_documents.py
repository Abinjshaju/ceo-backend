import io
import zipfile

from sqlalchemy import select

from ceo_backend.models import DocumentSection
from tests.conftest import ai, call, make_pdf, upload

PLAN_PDF = make_pdf([
    "Delivery plan\nThe loyalty launch is planned for 15 December 2026.\nTwo engineers are assigned.",
    "Budget\nThe approved budget is 18 lakh rupees including vendor fees.",
])


def docx_bytes() -> bytes:
    body = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Risks</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Payment gateway vendor may slip by four weeks.</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Risk</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc></w:tr>'
        '<w:tr><w:tc><w:p><w:r><w:t>Vendor delay</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Priya</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '</w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", body)
    return buffer.getvalue()


def xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Forecast"
    sheet.append(["Month", "Revenue (₹ lakh)"])
    sheet.append(["Jan", 4])
    sheet.append(["Feb", 6])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_pdf_upload_is_processed_with_page_references(client, auth, project_id, run_jobs, services, provider):
    body = upload(client, auth(), f"/projects/{project_id}", "plan.pdf", PLAN_PDF)
    status = client.get(f"/projects/{project_id}/documents/{body['document_id']}/status", headers=auth()).json()
    assert status["status"] == "processing" and status["searchable"] is False

    provider.structured("DocumentFacts", {"facts": []})
    run_jobs()
    status = client.get(f"/projects/{project_id}/documents/{body['document_id']}/status", headers=auth()).json()
    assert status["status"] == "ready" and status["searchable"] and status["page_count"] == 2
    assert status["latest_version"]["index_status"] == "done"
    with services.session() as db:
        pages = [s.page for s in db.scalars(select(DocumentSection).order_by(DocumentSection.ordinal))]
    assert pages == [1, 2]


def test_documents_are_searchable_and_cited(client, auth, project_id, run_jobs, provider):
    upload(client, auth(), f"/projects/{project_id}", "plan.pdf", PLAN_PDF)
    provider.structured("DocumentFacts", {"facts": []})
    run_jobs()

    captured = {}

    def answer(messages):
        result = messages[-1].content
        captured["result"] = result
        ref = __import__("json").loads(result)["results"][0]["ref"]
        return ai(f"The launch is planned for 15 December [{ref}].")

    provider.reply(ai("", call("search_project_documents", query="launch date December")), answer)
    body = client.post(f"/projects/{project_id}/messages",
                       json={"client_message_id": "d1", "content": "When is launch?"}, headers=auth()).json()
    citation = body["citations"][0]
    assert citation["kind"] == "document" and citation["page"] == 1 and citation["filename"] == "plan.pdf"
    assert "untrusted" in captured["result"]


def test_docx_xlsx_and_markdown_keep_structure(client, auth, project_id, run_jobs, services, provider):
    upload(client, auth(), f"/projects/{project_id}", "risks.docx", docx_bytes())
    upload(client, auth(), f"/projects/{project_id}", "forecast.xlsx", xlsx_bytes())
    upload(client, auth(), f"/projects/{project_id}", "notes.md", b"# Scope\nPilot in Mumbai first.\n\n# Team\nTwo engineers.")
    provider.structured("DocumentFacts", {"facts": []}, {"facts": []}, {"facts": []})
    run_jobs()
    with services.session() as db:
        sections = db.scalars(select(DocumentSection)).all()
    by_heading = {s.heading for s in sections}
    assert {"Risks", "Scope", "Team"} <= by_heading
    table = next(s for s in sections if "Vendor delay" in s.text)
    assert table.text.startswith("Risk | Owner")
    sheet = next(s for s in sections if s.sheet == "Forecast")
    assert sheet.text.splitlines()[0] == "Month | Revenue (₹ lakh)"


def test_company_documents_are_shared_with_projects(client, auth, project_id, company_ready, run_jobs, services):
    from ceo_backend.services.retrieval import search_documents
    import uuid

    upload(client, auth(), f"/workspaces/{company_ready}", "strategy.md", b"# Strategy\nRetention is the top priority for 2026.")
    run_jobs()
    listing = client.get(f"/projects/{project_id}", headers=auth()).json()["documents"]
    assert listing[0]["scope"] == "company"
    with services.session() as db:
        hits = search_documents(services, db, uuid.UUID(company_ready), uuid.UUID(project_id), "retention priority")
    assert hits and hits[0]["filename"] == "strategy.md"
    assert set(hits[0]["matched_by"]) == {"keyword", "semantic"}


def test_replacing_a_document_supersedes_the_old_version(client, auth, project_id, run_jobs, services, provider):
    import uuid

    from ceo_backend.services.retrieval import search_documents

    first = upload(client, auth(), f"/projects/{project_id}", "plan.md", b"# Plan\nLaunch in March.")
    provider.structured("DocumentFacts", {"facts": []}, {"facts": []})
    run_jobs()
    data = b"# Plan\nLaunch in December."
    target = client.post(f"/projects/{project_id}/documents/upload-url",
                         json={"filename": "plan.md", "size_bytes": len(data), "document_id": first["document_id"]},
                         headers=auth()).json()
    assert target["version"] == 2
    client.put(target["upload"]["url"], content=data, headers=target["upload"]["headers"])
    client.post(f"/projects/{project_id}/documents/{first['document_id']}/complete",
                json={"version_id": target["version_id"]}, headers=auth())
    run_jobs()
    status = client.get(f"/projects/{project_id}/documents/{first['document_id']}/status", headers=auth()).json()
    assert status["current_version"] == 2
    workspace = client.get(f"/projects/{project_id}", headers=auth()).json()
    with services.session() as db:
        project = uuid.UUID(project_id)
        from ceo_backend.models import Project

        wid = db.get(Project, project).workspace_id
        texts = [h["snippet"] for h in search_documents(services, db, wid, project, "launch")]
    assert any("December" in t for t in texts) and not any("March" in t for t in texts)
    assert workspace["documents"][0]["current_version"] == 2


def test_upload_validation(client, auth, project_id, services):
    bad_type = client.post(f"/projects/{project_id}/documents/upload-url",
                           json={"filename": "virus.exe", "size_bytes": 10}, headers=auth())
    assert bad_type.status_code == 422
    too_big = client.post(f"/projects/{project_id}/documents/upload-url",
                          json={"filename": "big.pdf", "size_bytes": services.settings.max_upload_bytes + 1}, headers=auth())
    assert too_big.status_code == 422

    target = client.post(f"/projects/{project_id}/documents/upload-url",
                         json={"filename": "a.txt", "size_bytes": 5}, headers=auth()).json()
    early = client.post(f"/projects/{project_id}/documents/{target['document_id']}/complete",
                        json={"version_id": target["version_id"]}, headers=auth())
    assert early.status_code == 409
    url = target["upload"]["url"]
    oversized = client.put(url, content=b"0123456789", headers=target["upload"]["headers"])
    assert oversized.status_code == 422
    tampered = client.put(url[:-3] + "abc", content=b"01234", headers=target["upload"]["headers"])
    assert tampered.status_code == 401


def test_unreadable_files_fail_without_retrying(client, auth, project_id, run_jobs):
    body = upload(client, auth(), f"/projects/{project_id}", "broken.pdf", b"%PDF-1.4 not really a pdf")
    run_jobs()
    status = client.get(f"/projects/{project_id}/documents/{body['document_id']}/status", headers=auth()).json()
    assert status["status"] == "failed"
    assert status["latest_version"]["error"]
    retried = client.post(f"/projects/{project_id}/documents/{body['document_id']}/retry", headers=auth())
    assert retried.status_code == 200 and retried.json()["status"] == "processing"
    run_jobs()
    status = client.get(f"/projects/{project_id}/documents/{body['document_id']}/status", headers=auth()).json()
    assert status["status"] == "failed"


def test_document_facts_are_proposed_and_conflicts_raised(client, auth, project_id, run_jobs, services, provider):
    upload(client, auth(), f"/projects/{project_id}", "plan.pdf", PLAN_PDF)

    def extract(prompt):
        text = prompt[1].content
        budget_section = int(text.split("[section ")[2].split(" ")[0].split("]")[0])
        first_section = int(text.split("[section ")[1].split(" ")[0].split("]")[0])
        return {"facts": [
            {"field": "investment", "value": "18 lakh rupees", "section_id": budget_section, "quote": "approved budget",
             "relation": "conflicts"},
            {"field": "name", "value": "Loyalty", "section_id": first_section, "quote": "x", "relation": "conflicts"},
            {"field": "risks", "value": "Engineering capacity and vendor fees", "section_id": budget_section,
             "quote": "x", "relation": "adds_detail"},
            {"field": "team_capacity", "value": "Two engineers", "section_id": first_section, "quote": "Two engineers",
             "relation": "new"},
        ]}

    provider.structured("DocumentFacts", extract)
    run_jobs()
    detail = client.get(f"/projects/{project_id}", headers=auth()).json()
    assert detail["facts"]["investment"]["value"] == "₹20 lakh"
    assert detail["facts"]["team_capacity"]["status"] == "proposed"
    assert detail["facts"]["team_capacity"]["source"]["kind"] == "document"
    assert detail["facts"]["team_capacity"]["source"]["ref"].startswith("doc:")
    questions = [q["description"] for q in detail["open_questions"]]
    assert len(questions) == 1 and "18 lakh" in questions[0]
    assert detail["facts"]["name"]["value"] == "Retention drive"
    assert detail["facts"]["risks"]["value"] == "Engineering capacity"


def test_deleting_a_document_reviews_dependent_facts(client, auth, project_id, run_jobs, provider, services):
    body = upload(client, auth(), f"/projects/{project_id}", "plan.pdf", PLAN_PDF)

    def extract(prompt):
        text = prompt[1].content
        section = int(text.split("[section ")[1].split(" ")[0].split("]")[0])
        return {"facts": [{"field": "team_capacity", "value": "Two engineers", "section_id": section, "quote": "x",
                           "relation": "new"}]}

    provider.structured("DocumentFacts", extract)
    run_jobs()
    assert client.get(f"/projects/{project_id}/facts", headers=auth()).json()["facts"]["team_capacity"]["status"] == "proposed"

    response = client.delete(f"/projects/{project_id}/documents/{body['document_id']}", headers=auth())
    assert response.status_code == 204
    facts = client.get(f"/projects/{project_id}/facts?history=true&field=team_capacity", headers=auth()).json()
    assert "team_capacity" not in facts["facts"]
    assert facts["history"][-1]["status"] == "retired"
    assert client.get(f"/projects/{project_id}/documents", headers=auth()).json()["documents"] == []
    with services.session() as db:
        assert db.scalars(select(DocumentSection)).all() == []


def test_tools_cannot_read_other_workspaces_sections(client, auth, project_id, run_jobs, provider, services):
    other_ws = client.get("/me", headers=auth("other@example.com")).json()["workspaces"][0]["id"]
    upload(client, auth("other@example.com"), f"/workspaces/{other_ws}", "secret.md", b"# Secret\nAcquisition target list.")
    run_jobs()
    with services.session() as db:
        secret_id = db.scalar(select(DocumentSection.id))
    provider.reply(ai("", call("read_document_section", section_id=secret_id)), ai("I can't find that section."))
    client.post(f"/projects/{project_id}/messages", json={"client_message_id": "x1", "content": "Read it"}, headers=auth())
    run = client.get(f"/projects/{project_id}/messages", headers=auth()).json()["messages"][-1]["run_id"]
    calls = client.get(f"/agent-runs/{run}", headers=auth()).json()["tool_calls"]
    assert calls == [{"name": "read_document_section", "ok": False, "duration_ms": calls[0]["duration_ms"]}]


def test_download_urls_are_signed_and_expire(client, auth, project_id, run_jobs, services):
    body = upload(client, auth(), f"/projects/{project_id}", "notes.txt", b"hello world")
    run_jobs()
    url = client.get(f"/projects/{project_id}/documents/{body['document_id']}/download-url", headers=auth()).json()["url"]
    response = client.get(url)
    assert response.status_code == 200 and response.content == b"hello world"
    assert "attachment" in response.headers["content-disposition"]

    expired = services.storage.sign({"op": "get", "k": "x", "exp": 1})
    assert client.get(f"/storage/objects/{expired}").status_code == 401
