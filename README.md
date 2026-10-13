# CEO Workspace

A decision workspace for CEOs: company and project onboarding, a project assistant that answers from documents and project history, durable project memory, and interactive briefs and scenarios.

This repository holds the whole app:

```text
frontend/      React app (Vite). Built into the container and served by the API.
src/           Python backend (FastAPI) and agents
migrations/    Database migrations (Alembic, Postgres)
tests/         Backend test suite
docs/          Build plan and the original product handoff
Dockerfile     One image: builds the frontend, installs the backend
compose.yaml   Postgres, migrations, web app, background worker
```

The design and its status are in [docs/backend-plan.md](docs/backend-plan.md).

## Run everything in containers

Requires Docker.

```sh
cp .env.example .env    # set GOOGLE_API_KEY, AUTH_JWT_SECRET and STORAGE_SIGNING_SECRET
docker compose up --build -d --wait
```

Open http://localhost:8774 and create an account.

| Service | What it does |
|---|---|
| `db` | Postgres 17 with pgvector, data in the `pgdata` volume |
| `migrate` | Applies database migrations, then exits |
| `app` | API under `/api` and the built frontend at `/`, on port 8774 |
| `worker` | Background jobs: document processing, memory, summaries, briefs |

Uploaded documents live in the `storage` volume, shared by `app` and `worker`. The containers run as a non-root user with a read-only filesystem.

```sh
docker compose logs -f app worker     # follow logs
docker compose up --build -d --wait   # rebuild after code changes
docker compose down                   # stop (data is kept)
docker compose down -v                # stop and delete all data
```

Generate the two secrets with, for example, `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`.

## Develop locally

Requires [uv](https://docs.astral.sh/uv/), Node.js 24, and Docker for the database.

```sh
docker compose up -d --wait db          # only Postgres
uv run ceo-backend migrate
uv run ceo-backend serve --reload       # API on http://127.0.0.1:8000

cd frontend && npm ci && npm run dev    # frontend with hot reload; proxies /api to :8000
```

With `JOBS_INLINE=true` (the default outside containers), background jobs run inside the API process, so no worker is needed while developing. `uv run ceo-backend serve` also serves `frontend/dist` if you've run `npm run build`.

## Sign-in

`AUTH_MODE=local` (the default) keeps email and password accounts on the server. Passwords are hashed with scrypt, and an account locks for 15 minutes after 10 wrong attempts. The server signs its own tokens with `AUTH_JWT_SECRET`. The frontend keeps the token in local storage and sends it as `Authorization: Bearer`.

To use an identity provider instead (Clerk, Auth0, Supabase, Cognito), set `AUTH_MODE=jwks` with `AUTH_JWKS_URL`, `AUTH_ISSUER`, and `AUTH_AUDIENCE`. The frontend would then need that provider's sign-in flow, which isn't wired up yet.

For scripts and API testing, `uv run ceo-backend dev-token --email you@example.com` prints a token.

## API

All endpoints are under `/api`; interactive docs are at `/docs`.

```sh
TOKEN=$(curl -s localhost:8774/api/auth/login -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"your-password"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
curl -s -H "Authorization: Bearer $TOKEN" localhost:8774/api/me
```

`client_message_id` on chat messages makes sends idempotent: retrying with the same id returns the stored answer instead of running the model again.

### Uploading documents

1. `POST /api/projects/{id}/documents/upload-url` with `filename` and `size_bytes`
2. `PUT` the file to the returned `upload.url` with the returned headers
3. `POST /api/projects/{id}/documents/{document_id}/complete` with `version_id`
4. Poll `GET /api/projects/{id}/documents/{document_id}/status` until `searchable` is true

Supported: PDF (including scanned PDFs, through Gemini OCR), DOCX, XLSX, CSV, TXT, and Markdown. Use `/api/workspaces/{id}/documents/...` for company-level documents shared with every project.

### Streaming events

Add `?stream=true` to a message `POST` to receive Server-Sent Events:

| Event | Data |
|---|---|
| `run_started` | `run_id`, `user_message_id` |
| `progress` | `stage`, a short label such as "Searching documents" |
| `text_delta` | `text` |
| `component_ready` | a validated component (`artifact_id`, `type`, `title`, `data`, `source_refs`, `project_version`) |
| `context_updated` | what changed, such as `project_version` and `facts` |
| `completed` | the full response contract |
| `failed` | `error`, plus `run_id` when a run had started |

### Spoken replies

When voice is on (the speaker button under the assistant), each new reply is read aloud: its opening, about 40 seconds, then "The rest is on screen". The red button stops the voice; the written answer is unaffected. Sending a message, dictating or closing the assistant also stops it.

1. `GET /api/messages/{id}/speech` returns what to read, as short parts, and the engine: `gemini`, or `browser` when speech is off on the server.
2. `GET /api/messages/{id}/speech/audio` streams Gemini's voice for those parts as raw PCM (`audio/L16`, 24 kHz). The browser plays it while it arrives, so the first words are heard about a second after the reply finishes. Stopping cancels the stream, and with it the generation.

Only stored assistant replies the user can read are spoken, so the endpoint can't voice arbitrary text. Speech uses `SPEECH_MODEL` (default `gemini-3.8-flash-lite-tts`) with `SPEECH_VOICE`; set `SPEECH_ENABLED=false` to use the browser's own voice instead. At launch pricing ($6 per million audio tokens, about 25 tokens a second) a 40-second reply costs about $0.006. If Gemini speech is unavailable, the browser's voice reads the reply sentence by sentence.

### Voice conversation

The mic button under the assistant starts a spoken conversation. The assistant listens; when you pause for about a second, what you said is transcribed and appears in the chat as your message (marked with a mic), and the reply is read aloud. Then it listens again. The mic is paused while it transcribes, thinks and speaks, so it never hears itself; the red Stop button silences it and it listens again straight away. Tap the mic button again, close the assistant or stay quiet for two minutes to end the conversation.

- The browser only measures loudness to find where a sentence starts and ends. Each sentence goes once to `POST /api/workspaces/{id}/transcriptions?language=en-US` as a 16 kHz mono WAV (at most about a minute) and returns `{text}`. Audio is not stored or logged.
- Transcription uses `TRANSCRIPTION_MODEL` (default `gemini-3.6-flash`, about 2 s for a 7-second question), with the company, project and owner names as hints so names come out right.
- The microphone needs HTTPS (or `localhost`) and the user's permission. It is open only during a voice conversation.

## Tests

The current results and bug history are in [docs/qa-report.md](docs/qa-report.md). Release checks and limitations are in [docs/releases/v0.0.1.md](docs/releases/v0.0.1.md).

```sh
uv run pytest                                   # SQLite
docker compose exec db psql -U ceo -c "CREATE DATABASE ceo_test"
TEST_DATABASE_URL=postgresql+psycopg://ceo:ceo@127.0.0.1:55432/ceo_test uv run pytest   # Postgres
```

Frontend regressions and build:

```sh
cd frontend
npm test
npm run build
```

For isolated HTTP or browser QA (no paid API calls), run these in two terminals from the project root:

```sh
uv run python -m tests.qa_browser_server
uv run python -m tests.qa_http_smoke
```

The fixture serves the built frontend at http://127.0.0.1:8787; sign in with `qa@example.invalid` / `QA-test-password-2026`. Its database and storage are temporary and are removed when the server exits. The HTTP smoke script mutates only that fixture.

Tests use a scripted model and a deterministic embedder, so they make no API calls. The Postgres run refuses any database whose name doesn't end in `_test`, because it resets the schema.

## Credits

The assistant's robot avatar is [agent-robot-avatar](https://github.com/CX-ArtLab/agent-robot-avatar) by CX ArtLab (MIT licensed, pinned to 0.5.2). The character design and its name belong to CX ArtLab. [frontend/src/avatar.jsx](frontend/src/avatar.jsx) maps conversation events to its expressions, and Settings has a preview where you can try every expression and change its appearance.
