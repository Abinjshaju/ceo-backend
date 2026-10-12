# CEO Workspace QA context

Updated on 2026-10-13; originally detected on 2026-10-12 from README, package manifests, source, and tests. Unconfirmed facts are explicitly marked unknown.

## Product

CEO decision workspace. Critical flows: sign up/sign in; complete company context; onboard a project; upload and retrieve documents; send project chat messages with citations; generate briefs and scenarios; archive/unarchive projects; enforce workspace isolation.

## Tech Stack

React 19.3.0, Vite 8.3.3; Python >=3.15, FastAPI, SQLAlchemy, Alembic. Local SQLite; container Postgres 17 with pgvector. Gemini chat/embeddings. Local or S3 storage. Docker Compose app and worker.

## Test Stack

pytest >=9.1.1 configured in pyproject.toml; tests/ contains backend unit and in-process HTTP integration tests. Temporary real SQLite database per test; optional TEST_DATABASE_URL ending in _test for Postgres. ScriptedChat and HashEmbedder replace external AI calls. Frontend uses Node test runner with Vite SSR in frontend/tests; run npm test (rendering, session storage, upload validation). Browser smoke uses computer-use accessibility snapshots; no repeatable CI browser harness yet. tests.qa_browser_server provides an isolated temporary SQLite app with scripted AI; tests.qa_http_smoke checks authenticated HTTP workflows on port 8787.

## CI/CD

No CI workflow detected. Deploy gating and artifact retention unknown.

## Environments

Running local app: http://localhost:8080. Development API documented at 127.0.0.1:8000. Production/staging unknown. Tests use isolated temporary databases and storage.

## Quality Goals

Session acceptance: all existing backend tests pass; frontend build exits 0; each browser check records expected versus observed state. Coverage percentage, flake rate, and performance budgets unknown; no targets invented.

## Risk Areas

| Area | Impact | Likelihood | Priority and reason |
|---|---|---|---|
| Auth and workspace isolation | High | Unknown | Test first; disclosure or loss of access |
| Document ingestion and retrieval | High | Unknown | Test first; incorrect or unavailable evidence |
| AI chat, memory, briefs | High | Unknown | Test first; unreliable decisions or duplicated operations |
| Frontend session and error recovery | Medium | Unknown | Important; interrupts user workflows |

Likelihood and business incident history need owner input; priorities reflect impact, not measured failure frequency.

## Team

Headcount, QA ownership, and developer-to-QA ratio unknown.

## Conventions

Backend tests: tests/test_*.py, shared pytest fixtures in tests/conftest.py. Browser checks use role/label or fresh accessibility indices. Preserve existing user data; synthetic invalid credentials only against the live app. Record results after milestones in docs/qa-report.md. Do not report a one-off browser check as a deterministic CI harness.
