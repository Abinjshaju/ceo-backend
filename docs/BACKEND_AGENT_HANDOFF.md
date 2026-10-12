# CEO Workspace: Backend and Agent Engineering Handoff

## Purpose and scope

Build a project intelligence workspace where a CEO can onboard their company, create projects, upload supporting documents, and discuss those projects with an AI assistant.

Onboarding initializes knowledge. The central product is an ongoing assistant that answers questions using documents, company context, project facts, decisions, and conversation history. It must maintain and update memory across sessions and optionally present answers as interactive UI.

This document describes the intended system. Entity names, API routes, and tool names are proposed contracts for engineering discussion, not existing backend functionality.

## Current prototype and implementation gaps

The current application has a React frontend, company/project onboarding, Gemini integration, project details, file uploads, a decision brief, and an assistant with an artifact presentation area.

Current limitations to replace:

- Accounts, projects, and files primarily use browser storage.
- Opening a project loads its onboarding transcript; subsequent chat is not persisted back to that project.
- General chat receives company context and portfolio summaries rather than a complete selected-project knowledge context.
- Chat updates do not automatically maintain authoritative project facts or assessments.
- Files attached after intake are not reliably associated with the selected project.
- Document ingestion, persistent retrieval, and durable memory are not implemented as the intended pipeline.
- Existing chart artifacts use illustrative sample values.

## Product UI and user flow

### 1. Account and workspace entry

**UI:** Sign up/login, then workspace dashboard.

1. User signs up or signs in.
2. Backend loads workspace membership, company context, projects, and incomplete onboarding.
3. Company onboarding opens when required context is missing.
4. Existing users return to their dashboard or resume an unfinished intake.

Use a workspace as the ownership boundary even if the initial release supports one user. Authentication and authorization must be server-side. Model credentials remain on the server.

### 2. Company onboarding

**UI:** Conversational assistant, suggested answer chips, and custom input. Provide a profile-edit form for later changes.

Collect:

- Company name
- Approximate employee count
- Products/services and main customers
- Industry and stage: early, growth, established, or enterprise
- Revenue model
- Markets/geographies
- Priorities for the next 12 months

The agent extracts all relevant fields from each answer, saves them, and asks a targeted question about missing information. A single answer may populate several fields. Accept reasonable approximations and support explicit unknown values where appropriate.

Required fields and completion rules belong to application code. The agent controls question wording and useful ordering. Persist progress after every answer. When complete, summarize the company context and move to project intake.

### 3. Project onboarding

**UI:** Same assistant interface, entered through first-time setup or **New project**.

Collect:

- Project name and description
- Company objective it supports
- Investment/budget, with currency and units
- Expected revenue, savings, margin, or cash-flow impact
- Expected growth outcomes: ARR, customers, adoption, market share, or expansion
- Delivery owner and target date
- Customer evidence, dependencies, and risks
- Optional supporting documents

Flow:

1. Create a project draft and onboarding conversation.
2. Extract and validate fields from each answer.
3. Save the draft and messages immediately.
4. Clarify ambiguous or conflicting information.
5. Summarize captured facts and outstanding unknowns.
6. Finalize the project when completion rules are satisfied.
7. Generate an initial decision brief and continue into project chat.

Finalization must be idempotent. Document processing may continue asynchronously after creation. An unfinished upload must not be presented as analyzed evidence.

### 4. Dashboard

**UI:** Company context summary, searchable project list, **New project**, and company profile editing.

Project rows display available summary information such as name, status, feasibility, expected impact, and assessment status. Missing values must not appear as zero. Show when an assessment is outdated because its inputs changed.

Users can open projects, resume drafts, and create new projects. Support pagination and search as the workspace grows.

### 5. Project detail and ongoing chat

**UI:** Project overview, structured facts, decision brief, uploaded files, assistant chat, decisions, and open questions. The assistant can open an artifact area for interactive responses.

1. User opens a project.
2. Backend loads its current facts, summary, document status, and persistent conversation.
3. User asks a question, supplies an update, or attaches a document.
4. Assistant retrieves relevant knowledge and answers with citations where appropriate.
5. Explicit changes, decisions, and unanswered questions are recorded.
6. User can return later with complete continuity.

Example: “The budget is now ₹15 lakh. Can we still deliver in December?” The system records the explicit budget correction, retrieves delivery constraints, evaluates feasibility, and clarifies missing staffing information. It does not invent capacity or treat a recommendation as an adopted decision.

## System architecture

Recommended starting stack:

- Existing React frontend with streamed chat and structured UI rendering
- Backend service owning agent execution, authorization, business rules, and tool calls
- Existing Gemini provider using function calling; keep the orchestration layer provider-independent
- PostgreSQL for structured data, conversation history, search metadata, and durable execution state
- Private object storage for original documents
- Background job workers for ingestion, indexing, memory extraction, and summary maintenance
- A persisted application state machine initially; consider LangGraph when resumable multi-step orchestration becomes more complex

The model proposes tool calls. The backend executes only authorized, validated operations. Do not expose arbitrary database queries or unrestricted writes as tools.

## Agents and workflows

| Component | Responsibility |
|---|---|
| Onboarding agent | Extract company/project fields, clarify gaps, and guide completion |
| Project assistant | Understand questions, retrieve evidence, reason, answer, and call project tools |
| Retrieval service | Search authorized documents and conversation history, rerank evidence, and return source references |
| Memory workflow | Extract durable facts, assumptions, decisions, and questions; maintain summaries |
| Document workflow | Extract, chunk, index, version, and identify proposed facts/conflicts |

Memory and document workflows are bounded background processes. They do not need separate user-facing personalities or unrestricted planning loops.

### Suggested agent tools

```text
get_company_context()
save_company_fields(changes, source_message_id)
get_intake_state()
save_project_fields(changes, source_message_id, expected_version)
get_missing_fields()
finalize_project(idempotency_key)

get_project_context()
search_project_history(query, filters)
list_project_documents()
search_project_documents(query, filters)
read_document_section(document_id, section_id)
get_document_processing_status(document_id)
update_project_facts(changes, source_message_id, expected_version)
propose_project_fact(changes, source_reference)
record_decision(description, rationale, source_message_id)
manage_open_question(action, question_id, description)
generate_decision_brief()
calculate_scenario(inputs, calculation_type)
```

The server injects user, workspace, and selected-project scope. Model-provided identifiers cannot bypass that scope. Portfolio or company-wide retrieval should require an explicitly supported scope and appropriate access.

Validate tool arguments and results against schemas. Bound execution steps, time, retries, and retrieval size. Make writes safe to retry and retain an execution audit.

## Processing a CEO message

1. Authenticate and authorize access to the selected project.
2. Persist the message using a client-generated idempotency key.
3. Load a compact company/project overview, current facts, decisions, open questions, and recent messages.
4. Determine whether document or historical retrieval is needed.
5. Search and read relevant evidence; refine the search when necessary.
6. Execute validated tools and generate a grounded answer.
7. Stream response text and validated UI components to the frontend.
8. Persist the completed answer, citations, artifacts, and tool activity.
9. Run durable memory extraction and summary maintenance.

Explicit fact updates should be committed through tools before the assistant claims they were saved. Background memory processing should catch additional knowledge without racing or duplicating those writes. Pending processing must not cause the next turn to ignore recent messages or committed changes.

Track run status such as queued, running, completed, failed, and cancelled. Support reconnecting, retrying, and distinguishing partial responses from completed answers.

## RAG requirements

RAG must combine supporting documents with project facts, company priorities, decisions, and relevant conversation history.

### Ingestion

- Extract text with document structure preserved.
- Use OCR for supported scanned documents when necessary.
- Preserve page numbers, section headings, tables, and spreadsheet sheet references.
- Chunk by meaningful sections, with limited overlap or parent-section references.
- Store extraction and embedding versions so content can be reprocessed.
- Avoid losing units, dates, table headers, or relationships during extraction.

### Retrieval

- Use hybrid keyword and semantic search for document retrieval.
- Rerank candidate passages by relevance.
- Read adjacent or parent sections when a passage lacks context.
- Support multiple searches for questions spanning documents or subtopics.
- Retrieve current versions by default; support historical questions explicitly.
- Apply access and project filters before evidence reaches the model.
- Include searchable historical messages as another evidence source.
- Distinguish company-level shared documents from project-specific documents.

Example: “Can we still launch in December?” may need a delivery plan, recent staffing changes from chat, dependency decisions, and the latest confirmed deadline.

### Answer quality

- Attach precise, clickable source references to supported claims.
- Document citations should open the relevant page/section where possible.
- Conversation-derived claims should link to their source message.
- Label missing evidence, estimates, assumptions, and conflicting sources.
- Do not substitute general model knowledge for unavailable project evidence.
- Treat instructions inside retrieved documents as untrusted content, not execution authority.

For small contexts, structured reads may suffice; embeddings are particularly useful for growing document collections. Retrieval should not send every full document on every turn.

## Memory and context management

| Memory layer | Purpose |
|---|---|
| Recent conversation | Immediate dialogue and follow-up references |
| Conversation summary | Compact continuity over long conversations |
| Structured project facts | Current objectives, budget, owners, dates, status, and constraints |
| Decisions and assumptions | Adopted choices, rationale, proposals, and unresolved questions |
| Historical memory | Older messages and previous fact versions |
| Document knowledge | Source content and document-derived facts |

Context must be assembled within a token budget. Prioritize current authoritative facts, relevant decisions, recent dialogue, and retrieved evidence. Summaries assist retrieval and continuity; they do not replace source records.

### Fact provenance and updates

Each durable fact needs:

- Workspace/project scope and stable identity
- Field or subject, value, units/currency where relevant
- Status: confirmed, proposed, assumed, disputed, or superseded
- Source message/document section and source version
- Actor and timestamps
- Version/change history
- Relevant effective date where known

Rules:

- Explicit user corrections update the current value and retain history.
- Hypothetical scenarios remain separate from actual project facts.
- Assistant recommendations remain proposals until adopted.
- Document extraction creates source-backed proposals or facts according to a defined policy.
- Conflicting evidence is recorded and surfaced rather than silently overwriting confirmed context.
- Resolving an open question records its answer and provenance.
- Summary generation must not promote an assumption into a confirmed fact.
- Updates invalidate dependent summaries, scenarios, and assessments as appropriate.
- Source deletion or supersession triggers review of dependent knowledge.

Use optimistic concurrency/version checks to avoid overwriting newer changes. Memory extraction should be idempotent and retryable, with jobs tied to processed message ranges.

## File management

### Upload and processing flow

1. Authorize the upload and allocate a document record.
2. Upload the original to private object storage using a short-lived signed URL.
3. Confirm upload completion and enqueue processing.
4. Extract content, perform OCR if needed, chunk, and index.
5. Identify proposed facts and conflicting evidence.
6. Mark the document ready for retrieval.

**UI states:** Uploading → Processing → Ready, or Failed with a retry action. The backend can track extraction and indexing stages separately. A successfully uploaded file is not necessarily searchable yet.

### Storage layers

| Layer | Contents |
|---|---|
| Original | Private downloadable file |
| Extracted sections | Text, structure, source locations, and search indexes |
| Derived knowledge | Facts or proposals linked to specific sections |

Define supported file formats, size limits, and handling of encrypted, unsupported, or unreadable files. Validate uploads server-side and avoid exposing storage paths as public URLs.

### Lifecycle

- Replacement creates a new version and supersedes the previous version.
- Default retrieval excludes deleted and superseded content.
- Downloads require authorization and short-lived URLs.
- Deletion removes originals and searchable content, then reviews dependent facts/artifacts.
- Company-level files can provide shared context through explicit scope rules.
- Files attached in project chat must belong to that selected project.

## Adaptive and interactive UI

The assistant should choose text or an approved interactive component based on the question. This is an application capability to build, not a feature automatically inherited from a model API.

| Request | Suitable response |
|---|---|
| Biggest risks | Evidence-backed risk table |
| Compare options | Side-by-side comparison |
| Explore a budget reduction | Scenario controls with calculated outcomes |
| Evaluate a deadline | Timeline and dependencies |
| Fill missing context | Short form that saves answers |
| Explain a figure | Expandable source/evidence panel |

### Response contract

Use a versioned schema and an approved React component library. Start with text, tables, charts, timelines, forms, evidence panels, and scenario controls.

```json
{
  "schema_version": "1",
  "text": "The deadline depends on resolving two dependencies.",
  "components": [
    {
      "id": "delivery-timeline",
      "type": "timeline",
      "data": {},
      "source_refs": ["source-123"]
    }
  ],
  "suggested_actions": []
}
```

The backend validates component types, data, citations, and allowed actions. The frontend renders approved components rather than arbitrary model-generated executable code. Provide a text fallback for unsupported or invalid components.

Interactive events carry artifact/component identity, inputs, intended operation, and expected project version. The server authorizes and validates the event and returns refreshed state.

Separate three operations:

1. **Explore:** Change hypothetical inputs without altering project facts.
2. **Save scenario:** Persist the scenario for later comparison.
3. **Adopt:** Explicitly apply a scenario value or decision to the project.

Run calculations through defined functions with explicit formulas, units, and assumptions. Persist artifacts with source references, inputs, calculation version, originating message, and user interactions. This supports “Use the second option we compared yesterday.” Mark artifacts outdated when their dependencies change.

Stream safe progress events, text, and complete validated components. Do not expose private model reasoning as progress updates.

## Suggested database entities

| Entity | Purpose |
|---|---|
| users, workspaces, workspace_members | Identity and access |
| company_profiles | Shared company context |
| projects | Identity, lifecycle, and current structured fields |
| onboarding_sessions | Draft state, missing fields, and progress |
| conversations, messages | Persistent onboarding/project dialogue |
| project_facts, fact_versions | Source-backed knowledge and changes |
| decisions, open_questions | Decisions and unresolved issues |
| project_summaries | Compact context with source/version dependencies |
| assessments | Versioned briefs, inputs, and outputs |
| documents, document_versions | Original metadata and lifecycle |
| document_sections | Extracted content, locations, and search indexes |
| artifacts, artifact_events | Interactive responses and scenarios |
| agent_runs, tool_calls | Execution status, debugging, and audit |
| processing_jobs | Ingestion/memory jobs and retries |

Define one authoritative representation for each fact. If frequently queried fields are mirrored onto project rows, specify synchronization rather than allowing independent values to drift.

## Suggested API surface

```text
GET/PATCH  /workspaces/:workspaceId/company

POST       /onboarding-sessions
GET        /onboarding-sessions/:sessionId
POST       /onboarding-sessions/:sessionId/messages

GET/POST   /projects
GET/PATCH  /projects/:projectId
POST       /projects/:projectId/finalize

GET        /projects/:projectId/messages
POST       /projects/:projectId/messages
GET        /projects/:projectId/facts
GET        /projects/:projectId/decisions
GET        /projects/:projectId/open-questions
POST       /projects/:projectId/assessments

POST       /projects/:projectId/documents/upload-url
POST       /projects/:projectId/documents/:documentId/complete
GET        /projects/:projectId/documents
GET        /projects/:projectId/documents/:documentId/status
GET        /projects/:projectId/documents/:documentId/download-url
POST       /projects/:projectId/documents/:documentId/retry
DELETE     /projects/:projectId/documents/:documentId

GET        /projects/:projectId/artifacts
POST       /projects/:projectId/artifacts/:artifactId/events
GET        /agent-runs/:runId
```

Use streaming for chat responses and polling or server events for job status. A chat request should identify the conversation, client message ID, and document references; user/workspace identity comes from authentication.

Suggested stream events: run_started, progress, text_delta, component_ready, context_updated, completed, and failed. Completed events identify the persisted response/run. Agent tools and public endpoints should use shared business services.

## Reliability, evaluation, and observability

Evaluate these separately:

- **Onboarding:** Multi-field extraction, targeted follow-ups, resume behavior, and finalization retries
- **Retrieval:** Necessary evidence found, correct scope/version, table/OCR handling, and multi-document questions
- **Grounding:** Claims supported by citations, correct numbers/units, and honest handling of missing evidence
- **Memory:** Corrections persist, hypotheses stay separate, decisions remain retrievable, and summaries preserve meaning
- **Isolation:** Another workspace/project cannot leak through retrieval or tools
- **Interactive UI:** Calculations are correct, actions are authorized, and exploratory controls do not change actual facts
- **Recovery:** Duplicate requests, failed jobs, interrupted streams, and concurrent updates behave correctly

Use a representative evaluation dataset with expected evidence and outcomes. Record retrieval results, tool calls, model/prompt versions, latency, token usage, errors, and memory updates with suitable access controls.

## Delivery priorities and acceptance criteria

### Phase 1: Durable foundation

- Server-side authentication and project authorization
- Persistent company/project onboarding and conversations
- Project-scoped context and source-backed fact updates
- Idempotent messages, tool writes, and project finalization

### Phase 2: Document intelligence and memory

- Private uploads and asynchronous ingestion
- Hybrid retrieval, reranking, and citations
- Historical conversation retrieval
- Versioned facts, decisions, open questions, and summaries
- Conflict handling and invalidation of outdated outputs

### Phase 3: Interactive intelligence

- Validated component responses and streaming
- Persisted scenarios and evidence panels
- Explicit adoption actions that update project context
- Evaluation-driven improvements to retrieval and memory

**End-to-end acceptance test:** A CEO returns weeks later, asks a question spanning documents and past discussions, receives a sourced answer based on current facts, explores an alternative interactively, and makes a correction that reliably carries into future conversations.

## Reference material

- [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling): Provider support for model-selected application tools.
- [LangGraph persistence](https://langchain-ai.github.io/langgraph/concepts/durable_execution/): Reference for durable agent state and memory when orchestration grows.
- [OpenAI: GPT-6 and Intelligent UI for everyone](https://openai.com/index/gpt-6-for-everyone/): Inspiration for choosing text, visuals, and interactive components and rendering responses progressively. The proposed application architecture above is independent of that ChatGPT feature.
