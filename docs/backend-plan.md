# CEO Workspace — Build Plan

This plan turns [BACKEND_AGENT_HANDOFF.md](BACKEND_AGENT_HANDOFF.md) into the app in this repository: the backend, plus the frontend copied from `ceo-pannel` into `frontend/` and connected to it. It records what is built, how the parts fit, and what is deliberately left for later.

## Stack

| Concern | Choice | Notes |
|---|---|---|
| API | FastAPI, sync endpoints | Streaming chat over Server-Sent Events |
| Database | PostgreSQL 17 + pgvector | SQLAlchemy 2 models, Alembic migrations. SQLite works for tests and quick local runs |
| Search | Postgres full-text search + pgvector, fused with reciprocal rank fusion | SQLite falls back to in-process keyword and cosine scoring |
| Models | Gemini via LangChain (`langchain-core`, `langchain-google-genai`) | Agents receive a `BaseChatModel`, so the provider can change |
| Embeddings | `gemini-embedding-001`, 768 dimensions | Optional; keyword search works without it |
| Auth | Built-in email/password accounts (`AUTH_MODE=local`), or any OIDC provider via JWKS, or an HS256 shared secret | Server verifies every request; passwords hashed with scrypt |
| Files | Private object storage: local disk with HMAC-signed URLs, or S3 with presigned URLs | Originals are never public |
| Background work | Postgres-backed job queue (`processing_jobs`) with a worker process | `JOBS_INLINE=true` runs jobs in-process for development |
| Frontend | React (Vite), built into the image and served by FastAPI at `/`, with the API under `/api` | One origin, so no CORS and relative upload links |
| Deployment | One Docker image; compose runs Postgres, migrations, the app, and the worker | Non-root, read-only filesystem |

## Ownership and access

- A **user** is identified by `(issuer, subject)` from a verified token. First sign-in creates the user, a personal **workspace**, and an owner **membership**.
- Every company profile, project, document, conversation, run, and artifact belongs to a workspace.
- Routes resolve the workspace or project from the path, then check membership. A missing or foreign resource returns 404.
- Agent tools are built per request with the workspace and project already bound. No tool accepts a workspace or project id from the model. Document and message ids passed to tools are re-checked against that scope.

## Agents and workflows

| Component | Kind | Tools |
|---|---|---|
| Company onboarding | Agent | `get_intake_state`, `save_company_fields`, `finalize_company_profile`, `suggest_answers` |
| Project onboarding | Agent | `get_intake_state`, `save_project_fields`, `finalize_project`, `suggest_answers` |
| Project assistant | Agent | `get_project_context`, `search_project_history`, `list_project_documents`, `search_project_documents`, `read_document_section`, `get_document_processing_status`, `update_project_facts`, `record_decision`, `manage_open_question`, `generate_decision_brief`, `calculate_scenario`, `present_component` |
| Company research | Bounded model call (Google Search grounding) | none |
| Memory extraction | Background job, structured output | none |
| Summary maintenance | Background job | none |
| Document ingestion | Background job: extract, chunk, embed, propose facts | none |
| Decision brief | Bounded model call, structured output | none |

That is 2 agents (onboarding has a company mode and a project mode) with 18 tools in total, plus 5 bounded workflows.

`propose_project_fact` from the handoff is folded into `update_project_facts` through its `status` argument (`confirmed`, `proposed`, `assumed`). `get_company_context` and `get_missing_fields` are folded into `get_intake_state`.

## Data model

| Table | Purpose |
|---|---|
| `users`, `workspaces`, `workspace_members` | Identity and access |
| `company_profiles` | One per workspace; field values with source and status, research results, version |
| `onboarding_sessions` | Company or project intake progress; resumable |
| `conversations`, `messages` | Persistent dialogue. `(conversation, client_message_id, role)` is unique, which makes sends idempotent |
| `projects` | Identity, lifecycle (`draft`, `active`, `archived`), and a `version` that increases on every fact change |
| `project_facts`, `fact_versions` | One current value per field with status, unit, source message or document section, actor; full history |
| `decisions`, `open_questions` | Adopted or proposed decisions; open and resolved questions with provenance |
| `project_summaries` | Rolling summary with the message and fact version it covers |
| `assessments` | Decision briefs with the project version they were computed from; outdated when the version moves |
| `documents`, `document_versions`, `document_sections` | File lifecycle, versions, and extracted sections with page, heading, sheet, full-text and vector search |
| `artifacts`, `artifact_events` | Interactive components and scenarios, with explore, save, adopt and form-submit events |
| `agent_runs`, `tool_calls` | Run status, model, token usage, and an audit of every tool call |
| `processing_jobs` | Ingestion, memory, summary and brief jobs with dedupe keys, retries and backoff |

## Fact rules

- Explicit user statements are saved through tools during the turn as `confirmed`. The previous value stays in `fact_versions`.
- Assistant suggestions are `proposed` until the user adopts them.
- Every write carries the expected project version. A stale version is rejected, so concurrent edits never overwrite each other silently.
- Memory extraction never overwrites a confirmed value. A conflicting value becomes an open question.
- Document extraction creates `proposed` facts linked to the document section. Conflicts with confirmed facts become open questions.
- Deleting a document removes its file and search content, retires proposed facts sourced from it, and opens a question for confirmed facts that relied on it.
- Scenario exploration never changes facts. Only an explicit adopt event does, with a version check.

## Processing a project message

1. Verify the token and project membership.
2. Save the user message by `client_message_id`. A repeat returns the stored answer.
3. Assemble context within a character budget: company profile, current facts with status, decisions, open questions, rolling summary, document statuses, and recent messages.
4. Run the tool loop, at most 8 rounds within a time limit. Every tool call is audited.
5. Stream `run_started`, `progress`, `text_delta`, `component_ready`, `context_updated`, then `completed` or `failed`.
6. Validate citations against evidence actually retrieved in this run, and drop any that weren't. Validate components against their schemas.
7. Save the answer, citations, components (as artifacts) and run status.
8. Queue memory extraction for the exchange, which then queues a summary update.

## Response contract

```json
{
  "schema_version": "1",
  "message_id": 42,
  "run_id": "…",
  "text": "December is at risk because … [doc:118]",
  "citations": [{"ref": "doc:118", "kind": "document", "document_id": "…", "page": 4, "heading": "Delivery plan"}],
  "components": [{"artifact_id": "…", "type": "timeline", "title": "…", "data": {}, "source_refs": ["doc:118"]}],
  "suggested_actions": [],
  "context": {"project_version": 7}
}
```

Component types: `table`, `chart`, `timeline`, `form`, `evidence_panel`, `scenario`. Tables and charts must cite sources, so no illustrative sample values are allowed.

## API

```text
GET    /health
GET    /me

GET    /workspaces/{wid}/company            PATCH /workspaces/{wid}/company
POST   /workspaces/{wid}/onboarding-sessions
GET    /onboarding-sessions/{sid}
POST   /onboarding-sessions/{sid}/messages            (?stream=true for SSE)

GET    /workspaces/{wid}/projects            (search, status, limit, offset)
GET    /projects/{pid}                      PATCH /projects/{pid}
POST   /projects/{pid}/finalize
GET    /projects/{pid}/messages             POST /projects/{pid}/messages (?stream=true)
GET    /projects/{pid}/facts                (?history=true)
GET    /projects/{pid}/decisions
GET    /projects/{pid}/open-questions
GET    /projects/{pid}/assessments/latest   POST /projects/{pid}/assessments

POST   /projects/{pid}/documents/upload-url
POST   /projects/{pid}/documents/{did}/complete
GET    /projects/{pid}/documents
GET    /projects/{pid}/documents/{did}/status
GET    /projects/{pid}/documents/{did}/download-url
POST   /projects/{pid}/documents/{did}/retry
DELETE /projects/{pid}/documents/{did}
(the same document routes exist under /workspaces/{wid}/documents for company-level files)

PUT    /storage/objects/{token}             GET /storage/objects/{token}   (local storage only)

GET    /projects/{pid}/artifacts
POST   /projects/{pid}/artifacts/{aid}/events
GET    /agent-runs/{rid}
```

## Status

All three phases are built. "Live" means it was exercised end to end against `gemini-3.6-flash` through the running API on Postgres; "tests" means the automated suite (64 tests, run on both SQLite and Postgres with a scripted model).

| Area | Built | Verified |
|---|---|---|
| Postgres schema, Alembic migration, search indexes | ✓ | tests, migration up/down, `alembic check` |
| Token verification, user provisioning, workspace isolation | ✓ | tests (HS256); JWKS path not run against a real provider |
| Company onboarding: multi-field extraction, unknowns, research, resume | ✓ | tests, live (research returned sourced findings) |
| Project onboarding and idempotent finalize | ✓ | tests, live |
| Project chat: versioned facts, decisions, open questions | ✓ | tests, live (budget correction kept history) |
| Idempotent messages, run status, tool audit, retries | ✓ | tests |
| Signed uploads, versions, replace, retry, delete with fact review | ✓ | tests, live |
| Extraction: PDF, DOCX, XLSX, CSV, TXT, Markdown; OCR for scanned PDFs | ✓ | tests; OCR live on an image-only PDF |
| Hybrid retrieval with fusion, neighbor reads, history search | ✓ | tests on both databases, live citations with page numbers |
| Proposed facts and conflicts from documents | ✓ | tests, live |
| Memory extraction, rolling summaries | ✓ | tests, live |
| Decision briefs that go out of date | ✓ | tests, live |
| Components, streaming, citations checked against evidence | ✓ | tests, live |
| Scenarios: explore, save, adopt; form submit | ✓ | tests, live (stale adopt rejected) |
| S3 storage | ✓ | not run against a real bucket |
| Password accounts, lockout, token issuing | ✓ | tests |
| Frontend connected to the API: sign-in, onboarding, project chat with streaming, artifacts, project page, files | ✓ | live in a real browser (Playwright) against the containers |
| Assistant avatar (agent-robot-avatar): expressions follow the conversation; appearance and every expression in Settings | ✓ | live in a real browser (Playwright): all 17 expressions, settings saved across reloads, reactions during real project turns |
| Spoken replies: Gemini Flash-Lite TTS for the opening of each reply, browser voice as fallback, Stop button | ✓ | API tests (parts, WAV, access, failure); live browser run against the containers |
| Voice conversation: sentence detection in the browser, Gemini 3.6 Flash transcription with workspace names, half-duplex turn-taking | ✓ | API tests (transcript, names, limits, access, failure); browser run with a fake microphone playing a recorded question |
| Container image and compose stack | ✓ | built and run locally; health checks pass |
| Model call time limit and one retry on dropped connections | ✓ | tests |

Citations can point at `doc:`, `msg:`, `fact:`, `decision:`, `question:` and `calc:` refs.

### Not in this build
- A model-based reranker (reciprocal rank fusion only)
- Workspace invitations and roles beyond owner and member
- An evaluation dataset scored against the live model
- Rate limiting and per-workspace quotas
- Antivirus scanning of uploads
- Identity-provider sign-in in the frontend (the backend accepts JWKS tokens; the UI only has password sign-in)
- Frontend unit tests (the UI is covered by the browser run only)

### Known behaviour to watch
- Project chat turns take 6 to 30 seconds on `gemini-3.6-flash`, mostly model reasoning. Streaming shows progress and text as it arrives.
- Memory extraction may record the assistant's own assessments as `proposed` facts (for example a delivery status of "at risk"). They are labelled as proposals, never as confirmed.
