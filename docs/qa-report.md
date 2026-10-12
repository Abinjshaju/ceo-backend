# CEO Workspace QA report

Last updated: 2026-10-13. Status: automated/API QA passing; QA-003 fixed; fresh browser QA blocked by unavailable automation transport.

## Scope and method

Target: this repository's CEO Workspace app. Installed qa-skills workflows: qa-project-context, api-testing, and agentic-browser-testing. Update this file after each testing milestone; retain failures, reproduction steps, and coverage limits. This is a session-maintained report, not a background monitor.

Goals: verify the existing offline backend suite, compile the frontend, and explore the running local UI using accessible controls and explicit outcomes. Do not change existing user data or call paid model services during exploration.

## Session log

| Step | Observation | Result |
|---|---|---|
| Discovery | React/Vite frontend, FastAPI backend, pytest fixtures with real temporary SQLite databases and scripted AI provider. Local port 8080 is listening. | Confirmed |
| Backend suite | `.venv/bin/pytest -q`: 64 passed in 4.94s, exit 0. Temporary SQLite and scripted external AI. | Pass |
| Frontend build | `npm run build` in frontend: Vite compiled 1570 modules, exit 0. | Pass |
| Browser sign-in | Chrome at http://localhost:8080/: heading “Sign in to your workspace”, labeled Email/Password, Sign in button. | Pass |
| Browser account tabs | Create account tab selected; “Create your account” heading and Name field appear. Sign in restores original form. No account submitted. | Pass |
| Browser input validation | Synthetic nonexistent email and password `short`: “Enter a valid email and a password of at least 8 characters.” Remains on sign-in, no dashboard. | Pass |
| Browser invalid credentials | Synthetic nonexistent email and sufficiently long synthetic password: “Email or password is incorrect.” Sign in button enabled; remains on sign-in. | Pass |
| Storage fallback reproduction | Import frontend/src/api.js with localStorage methods throwing. setToken followed by getToken returns null. | Fail: QA-001 |
| QA-001 retest | Reproduction above now prints `"actual":"qa-synthetic-token"`. Storage matrix (available / blocked / reads work but writes throw): sign-in keeps the token and sign-out clears it in all three; the pre-fix code fails the last two. | Pass |
| Blocked-storage browser flow | Chromium (Playwright 1.58) against the rebuilt container with `localStorage` throwing SecurityError: sign up reaches company setup; log out; sign in again; reload returns to sign-in (tab-only session). No console errors. First attempt could not log out at all. | Fail: QA-002, then Pass after fix |
| Normal-storage browser flow | Same flow with storage available: sign up, log out from setup, sign in, reload keeps the session. No console errors. | Pass |
| Backend suite, both databases | SQLite: 64 passed in 4.62s. Postgres 17 + pgvector (`ceo_test`): 64 passed in 8.41s. | Pass |

## Findings

### QA-001 — Sign-in cannot retain a session when localStorage is blocked

Severity: medium. Status: fixed and retested (see session log). Location: frontend/src/api.js, getToken/setToken.

Fix: when storage refuses the write, setToken keeps the token in memory and getToken returns it; sign-out clears both. The in-memory copy is used only after a failed write, so with working storage another tab's sign-out still applies. Regression check (one-off Node script, not yet in the repo): for each of the three storage scenarios, stub `window.localStorage`, import a fresh copy of api.js, call `setToken(token)` and expect `getToken()` to return it, then call `setToken(null)` and expect null.

Expected: the catch comment explicitly promises that “the session then lasts for this tab only.” A token saved during sign-in must therefore remain available for the next authenticated request.

Original pre-fix observation: setToken silently discards the token; getToken returns null. App.jsx calls loadMe after sign-in, and /me receives no Authorization header. This implies a return to sign-in in this condition; the storage behavior was executed directly, while the complete restricted-storage browser flow has not been exercised.

Reproduction from project root:

```sh
node --input-type=module -e 'globalThis.window={localStorage:{getItem(){throw new Error("storage blocked")},setItem(){throw new Error("storage blocked")},removeItem(){throw new Error("storage blocked")}}};const api=await import("./frontend/src/api.js");api.setToken("qa-synthetic-token");console.log(JSON.stringify({scenario:"localStorage unavailable",expected:"qa-synthetic-token",actual:api.getToken()}));'
```

Original pre-fix output: `{"scenario":"localStorage unavailable","expected":"qa-synthetic-token","actual":null}`. Current storage regression tests pass.

The in-memory fallback is implemented. Storage regression checks are now committed as frontend/tests/session.test.mjs.

### QA-002 — No way to log out until setup is finished

Severity: medium. Status: fixed and retested. Location: frontend/src/assistant.jsx (dialog header), frontend/src/App.jsx.

Observed (found while retesting QA-001): until the company profile is complete and a first project exists, the assistant dialog has no close button and its overlay blocks the page. Log out lives in the sidebar profile menu behind it, so a new account is stuck in setup. Because the token persists, reloading doesn't help. Playwright: clicking `.sidebar-profile` timed out with "assistant-modal … intercepts pointer events".

Fix: while the dialog can't be closed, its header shows a Log out button (accessible name "Log out"). Once setup is complete, the close button returns and log out stays in the sidebar.

## Covered backend behaviors

The passing suite includes local signup/login and lockout, workspace/project access isolation, company/project onboarding and version conflicts, upload validation and signed downloads, document replacement/deletion and citations, project chat idempotency and retries, streaming failures/completion, memory extraction, scenarios, artifact validation, project archival/deletion, and financial calculations. These are backend checks, not proof that each corresponding UI flow works.

## Coverage limits

Live Gemini response quality, OCR, external research, S3, performance, full accessibility compliance, and cross-browser coverage are not verified in this report. Postgres is now covered by the backend suite. Authenticated browser coverage is limited to sign-up, log out, sign-in, and reload; company setup, project creation, uploads, and chat still need a dedicated disposable browser fixture. The retests created disposable accounts (`ui-storage-…@example.com`, `ui-normal-…@example.com`) in the local container database; `docker compose down -v` removes them. No other records were modified.

The browser pass used accessibility snapshots and explicit visible outcomes, but is a one-off smoke session, not the skill's fully seeded, model-pinned CI harness. No CI gate, response-schema audit, coverage percentage, or flake measurement is claimed. Project context records unknown team/goals rather than inventing them.

Existing working-tree changes predate this session and were preserved. The frontend build regenerated ignored dist output.

## Next session and maintenance

1. Storage regressions are now in frontend/tests/session.test.mjs; keep running npm test.
2. Seed an isolated browser test environment with a scripted AI provider; verify login → company setup → project intake → document upload → cited chat.
3. Validate live-provider behavior separately.
4. After each new testing milestone, update this report's date, session log, findings, and coverage limits. Do not erase unresolved findings; mark fixed only with retest evidence. This file updates during active QA work; no background process is installed.

## Follow-up — project assistant entry and artifact rendering

Updated 2026-10-12. User reported three duplicate agent buttons and broken references/tables/charts.

Changes:
- Removed the topbar assistant button and project-detail “Discuss with assistant / Continue intake” button. The purple circular sparkle launcher remains; selecting a project still targets its conversation.
- Replaced the hand-written Markdown subset with react-markdown + remark-gfm, including Markdown tables, fenced code, lists, and citations within table cells and emphasis. Model HTML remains non-executable.
- Replaced manual chart SVGs with lazy-loaded Recharts bar/line/pie charts, responsive sizing, tooltips, legends, axis labels, and an expandable chart-data table. Missing points stay missing instead of becoming fabricated zero values.
- Added artifact-local citation metadata to component events and saved replies. Artifact-only sources are retained even when final prose contains no citation. Saved artifacts resolve sources from their associated message. The frontend also processes components on completion, including idempotent replay.
- Citation links navigate to expandable source details. Switching artifacts resets content scroll and source disclosure state.
- Reject non-finite chart values on the backend.

Evidence:
- New backend regression failed before the fix: artifact-only references produced an empty citations array. It passes after the fix and checks both the reply and persisted artifact.
- Backend: 65 passed in 4.72s.
- Frontend: 3 rendering regressions passed, covering Markdown table citations, literal code and unsafe HTML, and saved artifact references without chat history.
- Production frontend build passed; chart code split into a separate ~407 kB chunk, main chunk ~456 kB.
- Browser fixture at /tests/preview.html: Markdown/table source link navigated to the source and expanded the recorded investment; bar, line, and pie views rendered, with chart-data values 20 and 35 verified. Visual inspection performed. Fixture uses synthetic data and is excluded from the Docker production frontend build.

Limits: no live model output was used for this follow-up. This fixes verified citation transport and rendering defects, not every possible model-generated layout. Older artifacts whose metadata was already discarded may need regeneration. QA-001 was subsequently fixed and retested (see Findings). Container rebuild/application status follows below.

Follow-up verification: timeline rendering also confirmed in the browser (Launch, 2026-12-01, Planned). Docker image rebuild succeeded with the updated frontend and backend. No database migration was added or required.

Application complete: `docker compose up -d --no-deps --wait app worker` succeeded; app and worker reported Healthy. Updated version is running at http://localhost:8080. Final frontend regression rerun: 3 passed. Source search confirms one remaining `Open assistant` launcher and no project-detail/topbar duplicates. Existing volumes preserved.


## Full QA rerun — 2026-10-13, v0.0.1 candidate

| Check | Evidence | Result |
|---|---|---|
| SQLite backend suite | 73 passed in 6.55s | Pass |
| Postgres 17 + pgvector suite | Dedicated ceo_test database: 73 passed in 19.18s | Pass |
| Complete frontend suite | 9 tests passed (3 rendering, 6 storage/upload) | Pass |
| New storage/upload regressions | Before fix: removal-blocked storage logout failed; after fix: 6 passed | Pass after QA-003 fix |
| Production npm dependency audit | npm audit --omit=dev --audit-level=high: 0 vulnerabilities | Pass |
| Python syntax | compileall -q src tests: exit 0 | Pass |
| Authenticated HTTP smoke | 31 checks: assets, auth, project retrieval, SSE tables/charts/scenarios, citations, retries, scenario exploration, archival, workspace isolation | Pass |
| Frontend production build | Vite build exits 0, largest chunk approximately 407 kB | Pass |
| Container inventory | App and database healthy; worker running | Pass |
| Migration forward/rollback | ceo_migration_20261013_test: upgrade head → downgrade base → upgrade head, all exit 0 | Pass |
| ORM schema drift | alembic check: no new operations. pgvector reflection warning; vector column checked separately | Pass with custom-type reflection limitation |
| Fresh browser journeys | Browser tool transport closed; reset failed. Isolated fixture ready for later retest | Blocked; not passed |

### QA-003 — Logout reuses a token when storage refuses removal

Severity: medium. Status: fixed; regression verified. Location: frontend/src/api.js.

Reproduction: localStorage getItem/setItem work but removeItem throws. Sign in with setToken(token), then sign out with setToken(null). Before fix, getToken returns the old token instead of null. Failing automated regression captured in frontend/tests/session.test.mjs.

Fix: retain an explicit in-memory fallback flag, including a null logout marker, after storage throws. Working storage still observes another tab's sign-out. All four storage modes (available, blocked, write-blocked, remove-blocked), cross-tab sign-out and upload boundaries now pass. Reload may expose an inaccessible-to-remove stored token if the browser later changes its storage policy; no server-side token revocation is currently implemented.


### QA-004 — Artifact-only citations disappear

Severity: medium. Status: previously fixed, regression passed again on SQLite and Postgres. When the final prose does not cite a chart/table source, the artifact must still carry its own citation metadata. The saved artifact regression verifies this. Legacy artifacts generated before the fix may require regeneration.

### QA-005 — Duplicate project assistant entry buttons

Severity: low. Status: previously fixed. Source inspection confirms only the purple sparkle launcher remains; no fresh authenticated visual verification was possible in this run.

## Current verification limits

A full application-wide guarantee is not supported by these results. Fresh visual, responsive, keyboard, microphone, and screen-reader tests are blocked by the disconnected browser-control tool. Live Gemini quality, actual transcription/TTS audio quality, OCR, external research, S3, sustained load, Python dependency vulnerability scanning, and production monitoring/rollback are not verified. Speech/transcription contracts, failure paths and access isolation are covered with scripted providers in the backend suite.

No existing user projects were modified. The isolated HTTP checks use temporary SQLite data and a scripted model. The Postgres suite resets only ceo_test; migration checks used ceo_migration_20261013_test. Direct SQL confirmed vector(768), matching the ORM constant. No remote push or release tag is requested.


Final local application verification: Docker image rebuilt successfully; app and worker recreated, with Compose wait reporting success. Local app is available at http://localhost:8080. Database and document storage volumes preserved. Full frontend suite: 9 passed in 0.82s. HTTP smoke: 31 checks passed again with unique per-run message/account IDs. Candidate Git files were scanned for common credential/private-key patterns with no matches; .env, local databases, storage, virtual environment, node_modules and dist remain ignored.

Git scope: snapshot the current application, tests, installed QA skills, and updated README/project QA/release docs with requested commit message v0.0.1. Commit metadata is reported in the assistant response; this document intentionally does not embed its own future commit hash.

Deployed HTTP retest: /api/health reports ok, /healthz returns 200, and all five frontend assets referenced by the served index return 200.
