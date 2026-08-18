# Dodam Product Lifecycle Baseline

## 1. Status and audit timestamp

- Audit unit: U6 Product lifecycle baseline and deployment decision only.
- Audit timestamp: 2026-08-18 KST.
- Method: static code and document audit, focused deterministic tests, and official platform documentation check.
- Original U6 code changes: none.
- Original allowed artifact written: `docs/PRODUCT_LIFECYCLE_BASELINE.md`.
- Post-U6 update: U7 lifecycle hardening, U8 CI gates, U9 deployment package,
  and U10 external deployment were later completed and are summarized in this
  document for current portfolio/deployment accuracy.

Evidence boundaries:

- Retrieval correctness remains U1-U4 evidence.
- Generated grounding and display cleanliness remain U5 evidence.
- This document covers current product lifecycle implementation, recovery gaps, and deploy topology choice.
- Existing deployment files, local tests, or pricing pages are not proof of deployed production operation.
- U10 smoke proves only the deployed endpoints checked at the timestamp listed
  below; it does not prove user traffic, SLA, continuous monitoring, or full
  browser E2E.

Current U10 deployment smoke, checked on 2026-08-18 KST:

- Backend: `https://dodam-backend.onrender.com`
- Backend readiness: `GET /health/ready` returned HTTP 200 with database and
  schema checks ok.
- Frontend: `https://dodam-frontend.vercel.app`
- Frontend HTTP smoke: `GET /` returned HTTP 200.
- Database: Supabase PostgreSQL.
- Backend host: Render.
- Frontend host: Vercel.

Current chat/product lifecycle hardening record, updated on 2026-08-18 KST:

- Backend `f67663a`: added recommendation/eligibility idempotency keys, stale
  processing cleanup, request lifecycle schema/test coverage, and startup stale
  request maintenance.
- Frontend `2fe3984`: sends AI request idempotency keys for recommendation and
  eligibility requests initiated from chat.
- Frontend `50690f2`: guards duplicate chat submit/recommendation/eligibility
  actions in the UI and keeps active eligibility actions disabled while a
  request is in flight.
- Frontend `f1db355` and `305562d`: add and document Playwright browser smoke
  dependency for chat page deployment checks.
- Frontend `3315f52`: updates frontend dependencies that were covered by npm
  advisory checks; local `npm audit --json` returned 0 vulnerabilities after
  the update.

## 2. Repository revisions and dirty-worktree boundary

Backend repository:

- Path: `/Users/hb/Documents/kosa-course/projects/mini-2/back`
- Branch: `refactor/chat-handler-result-lifecycle`
- U6 HEAD: `89960ee`
- Current deployed backend code HEAD: `b2318d4`
- Latest backend documentation HEAD before this update: `32cb97a`
- Current status after U10 documentation update: this document and deployment
  runbooks may be modified until committed.

Frontend repository:

- Path: `/Users/hb/Documents/kosa-course/projects/mini-2/front`
- Branch: `chore/add-playwright-test` for the latest pushed chat hardening and
  advisory follow-up; `develop` remains the earlier deployed baseline branch.
- U6 HEAD: `4d3eb30`
- Current deployed frontend HEAD: `a2901fc`
- Latest pushed chat hardening/advisory HEAD: `3315f52`
- Current status after advisory update push: clean on
  `chore/add-playwright-test`.

Boundary:

- Frontend dirty files were read as current implementation evidence and were not overwritten, staged, stashed, reverted, formatted, or cleaned.
- Backend was clean before this U6 artifact. This document is the only intended backend change.
- U10 frontend deployment was performed from a clean temporary worktree at
  `a2901fc`, so the user-owned dirty frontend files were not included in the
  deployed artifact.
- The later frontend chat hardening and advisory commits have been pushed to
  `chore/add-playwright-test`, but this document does not claim that Vercel
  production has been redeployed from `3315f52`.

## 3. End-to-end user lifecycle trace

### 3.1 Chat SSE path

Current implemented path:

1. User enters text in `front/app/chat/page.js`; `finalizeSend()` creates an idempotency key and temporary assistant message.
2. Frontend calls `front/apis/chatStreamClient.js::sendMessageStream()` against `POST /api/v1/chat/sessions/{id}/messages/stream`.
3. Backend `app/api/chat_controller.py` validates session ownership before opening SSE.
4. `app/services/chat/chat_service.py::send_message_stream()` saves the user message, creates `chat_request` with status `processing`, and emits `accepted` with `request_id`.
5. Chat routing runs through `app/services/chat/_routing.py` and branch handlers. Progress, intent, and token events are streamed.
6. On success, backend persists assistant output, policy links, evidences, session slot, `last_message_at`, and marks `chat_request` `completed` with `response_payload_json`.
7. Frontend replaces the provisional assistant message on `done`, clears the pending request from `sessionStorage`, and renders policies, evidences, eligibility cards, retry controls, and progress state.
8. If the stream ends without terminal `done`, frontend polls `GET /api/v1/chat/requests/{request_id}` for recovery. On refresh it also checks `GET /api/v1/chat/sessions/{id}/requests/incomplete/latest`.
9. On backend startup, `app/main.py` calls `ChatRequestRepository.mark_stale_processing_failed()` to convert old processing chat requests to retryable failed status.

Status: partial but strongest current lifecycle. It has idempotency, request status recovery, stale startup cleanup, and focused tests. Process death during in-memory background disconnect recovery is still only eventually converted by startup stale cleanup, not resumed.

### 3.2 Recommendation request path

Current implemented path:

1. Frontend recommendation filling in `front/app/chat/page.js` calls `createRecommendationRequest()` and then polls `getRecommendationResult()`.
2. `front/apis/recommendationApi.js` posts to `/api/v1/recommendations/requests`, extracts `request_id`, and normalizes loading/done/error/follow_up states.
3. Backend `app/api/ai_request_controller.py` creates a `recommendation_request`, marks it `PROCESSING`, commits, then schedules `process_ai_condition_request()` as a FastAPI background task.
4. `AiRequestLifecycleService.process_condition_request()` runs Condition Agent, follow-up gate, `RecommendationGraphRunner`, result normalization, and writes `result_json`.
5. `RecommendationGraphRunner` executes candidate search, rule filter, candidate save, policy assessment, assessment save, result build, LLM rerank, rerank save, and final result.
6. `GET /api/v1/recommendations/requests/{request_id}` reads stored status/result only; it does not rerun graph, LLM, assessment, or RAG.
7. Frontend renders recommendation cards and condition-filling questions.

Status: partial. Request ID, ownership check on read, DB status/result,
timeout-to-failed, idempotency key handling, stale processing cleanup, and
polling exist after U7 hardening. Frontend `50690f2` also blocks duplicate
chat-triggered recommendation submits. Refresh recovery for the dedicated
recommendation page still depends on carrying the `request_id` in the URL and
polling stored state; deployed browser E2E is not yet verified.

### 3.3 Eligibility request path

Current implemented path:

1. Frontend eligibility actions call `front/apis/eligibilityApi.js::createRequest()`, then poll `getResult()`.
2. Backend `POST /api/v1/eligibility/requests` validates optional chat session ownership, creates `eligibility_request`, marks it `PROCESSING`, commits, and schedules `process_ai_condition_request()`.
3. `AiRequestLifecycleService` resolves policy, runs Condition Agent, saves assessment evidence via `PolicyAssessmentRepository`, writes `eligibility_request.result_json`, and marks terminal status.
4. `GET /api/v1/eligibility/requests/{request_id}` verifies user ownership and returns stored result. If linked to a chat session, it persists a terminal eligibility result message once.
5. Frontend polls up to 15 attempts and renders `EligibilityCardChat`.

Status: partial. Request ID, chat-source correlation, owner check, DB
result/status, idempotency key handling, stale processing cleanup, and polling
exist after U7 hardening. Frontend `50690f2` disables duplicate eligibility
actions in chat/result cards while a request is in flight. Deployed browser E2E
for this path is still not verified.

### 3.4 Recommendation and eligibility SSE path

Current implemented path:

- `POST /api/v1/recommendations/requests/stream` and `POST /api/v1/eligibility/requests/stream` stream progress and terminal result.
- Frontend has generic `postSseStream()`.

Status: partial. These endpoints are useful for progress display, but the request is processed inside the stream task. If the stream is interrupted, there is no explicit accepted request replay contract equivalent to chat SSE. General POST plus GET polling is currently the safer recovery path for recommendation and eligibility.

## 4. Lifecycle status matrix

The matrix below is the original U6 lifecycle audit snapshot. Later U7-U10 work
changed deployment and recovery evidence; current deployed state is summarized
in sections 1, 8, 9, and "Verification after U10 deployment" below.

| Stage | Backend evidence | Frontend evidence | Persisted state | Failure/recovery behavior | Test evidence | Status | Gap and impact |
|---|---|---|---|---|---|---|---|
| User input and auth gate | `CurrentUserDep` in chat/recommendation/eligibility controllers; `ensure_owned_session()` for chat and chat-linked eligibility | `app/chat/page.js` checks authenticated state before chat/recommendation/eligibility actions | User id attached to request/session rows | Unauthenticated UI blocks; backend rejects via dependency | `test_chat_controller.py`, `test_ai_request_controller.py` ownership cases | implemented | CORS is permissive and should be tightened before public deployment. |
| Chat submit/SSE accepted | `send_message_stream()` saves user message and `chat_request`, emits accepted request_id | `sendMessageStream()` handles accepted/requestId and provisional message | `chat_request.status=processing`, user message row | If no accepted request_id, UI cannot recover and shows failed state | `test_send_message_stream_emits_tokens_then_done`, controller SSE test | implemented | Accepted-before-result contract is chat-only. |
| Chat correlation/idempotency | Unique `(chat_session_id, idempotency_key)` and replay of existing completed request | `createIdempotencyKey()`, pending request storage | `chat_request.idempotency_key`, `response_payload_json` | Completed duplicate returns saved payload; processing duplicate emits accepted | `test_send_message_stream_idempotency_completed_returns_saved_payload` | partial | Processing duplicate does not attach to live stream; frontend must poll by request id. |
| Chat graph/service execution | `_run_chat()` branch routing, policy/evidence persistence helpers | Progress, intent, token, card rendering in chat page | `chat_message`, `chat_message_policy`, `chat_message_evidence`, session slot | Graph error marks request failed with user-safe message | chat service/controller tests | partial | Full browser E2E with real backend/DB was not run in U6. |
| Chat DB result save | `mark_completed()` saves assistant_message_id and `response_payload_json` | `applyCompletedChatPayload()` replaces provisional message and clears pending | `chat_request`, message/evidence/policy rows | Persist failure marks failed and does not emit done | `test_send_message_stream_final_persist_failure_does_not_emit_done` | implemented | No deployed DB migration proof. |
| Chat SSE disconnect recovery | `CancelledError` path continues or schedules disconnect recovery; stale startup cleanup | Polls request status after incomplete stream and on refresh | Existing request remains `processing` until completed/failed/stale | Refresh can recover completed payload or show failed/cancelled | frontend recovery code inspected; backend unit coverage for status endpoints | partial | In-process recovery depends on process survival; restart only fails stale request after cutoff, not resume. |
| Recommendation create/poll | `POST /recommendations/requests`, background task, `GET /requests/{id}` reads only stored result; U7 adds idempotency and stale cleanup | `createRecommendationRequest()`, `getRecommendationResult()`, 15x polling; chat-triggered duplicate submit guarded | `recommendation_request` with parsed/merged/result JSON, idempotency key, and status | Timeout/background exception marks failed; stale processing rows are failed on startup; UI maps failed to error | focused backend tests and chat recommendation branch tests | partial | Deployed browser E2E and dedicated page refresh behavior are not yet verified. |
| Recommendation graph and fallback | `RecommendationGraphRunner`, normalizer, rerank fallback service | Recommendation cards consume normalized result | `recommendation_candidate`, `policy_assessment`, `recommendation_request.result_json` | LLM rerank failure falls back to rule/assessment result | retrieval and recommendation tests exist; not all run in U6 | partial | Fallback is not exposed as a first-class user-visible AI-vs-deterministic provenance flag. |
| Eligibility create/poll | `POST /eligibility/requests`, `GET /eligibility/requests/{id}`, chat source ref ownership; U7 adds idempotency and stale cleanup | `eligibilityApi.createRequest()`, `fetchEligibilityResult()`; chat/result-card duplicate actions guarded | `eligibility_request`, idempotency key, `policy_assessment`, optional chat assistant message | Failed status masks internal error; stale processing rows are failed on startup; polling stops on terminal | `test_ai_request_controller.py` eligibility cases | partial | Deployed browser E2E is not yet verified; polling state remains component-local. |
| Failure masking | `AI_REQUEST_USER_ERROR_MESSAGE`, `_safe_error_message()`, chat retryable status | User-facing Korean error messages and retry UI | error message stored on request rows | Internal error details not returned for failed eligibility | `test_eligibility_result_response_masks_error_message` | implemented | Logs may still contain exceptions; U11 should define privacy-safe logging. |
| Process restart stale recovery | Startup marks stale `chat_request`, `recommendation_request`, and `eligibility_request` processing rows failed | Refresh/polling sees failed or retryable request state depending on flow | request tables | Old processing rows become failed after cutoff | `test_main_lifespan.py`, request repository tests | partial | Startup cleanup fails stale work rather than resuming it. |
| Timeout/retry | AI background timeout 360s; DB statement timeout 60s; chat failed requests retryable unless invalid input | Frontend recovery max wait 45s, retry creates new idempotency key if recovery fails | Failed request rows | Retry is mostly UI-level new request; not provider-level retry policy | focused tests | partial | Retry taxonomy for LLM/embedding/DB failures is not explicit. |
| Frontend result display | Backend response schemas include policies/evidences/cards | `AssistantMessage`, `EligibilityCardChat`, recommendation card components | Chat messages and request result JSON | Failed/provisional states shown with retry | frontend progress tests only | partial | Visual/browser E2E was not run; current dirty UI files are user-owned. |
| Backend/frontend CI | GitHub workflows exist only for PR title/branch/linked issue/Discord/issue close | Same | None | No deterministic build/test gate in workflow | Local tests only | missing | U8 must add release-quality CI; current workflows do not prove app correctness. |
| Build/deploy package | No backend Dockerfile/Compose/provider manifest found; frontend has Next.js package scripts and no `vercel.json` | Next.js `build/start`; `NEXT_PUBLIC_API_BASE_URL` env example | None | No health/readiness route found | Not run | missing | U9 must add deployment package and health/readiness before deploy-ready claim. |

## 5. Existing strengths

- Chat SSE has the best current product lifecycle baseline: accepted request id, DB-backed status, idempotency key, completed-payload replay, retryable failure state, frontend pending request storage, and refresh recovery.
- Recommendation and eligibility use `request_id`, idempotency keys, stale
  cleanup, and DB status/result tables, so polling after normal POST is safer
  than purely synchronous generation and duplicate submit can converge on an
  existing request.
- Backend separates `RequestStatus`, user-facing statuses, assessment state, and frontend loading variants in docs and schemas.
- Chat evidence and policy links are normalized into dedicated rows, reducing reliance on opaque `structured_json`.
- Focused deterministic tests cover many lifecycle edges without external AI
  calls, and frontend chat progress tests plus local build/lint/audit checks
  have been run after the latest chat/advisory updates.
- Secrets are represented through env names only; U6 did not print or store secret values.

## 6. Productization gaps ordered by severity

1. Deployed full browser E2E is still not verified.
   - Impact: local tests and HTTP smoke do not prove signup/login, chat submit,
     recommendation, eligibility, SSE recovery, and fallback display all work
     together on Vercel + Render + Supabase.
   - Next blocker: run an explicit deployed browser/API scenario without
     exposing secrets or user data.

2. Recommendation/eligibility SSE endpoints are not as recoverable as chat SSE.
   - Impact: progress stream disconnect can produce ambiguous user state unless the app uses POST+GET polling.
   - Current safer path: POST request creation plus GET polling.

3. Fallback provenance is not a first-class display contract across all flows.
   - Impact: deterministic fallback, LLM rerank success, and degraded output can be blurred in portfolio/product claims.
   - Future scope should expose safe provenance without leaking prompts or raw user data.

4. Public deployment privacy and observability are not defined.
   - Impact: request correlation exists, but metric labels/log boundaries are not yet enforced.
   - U11 blocker.

5. Platform rollback still does not cover DB migration rollback.
   - Impact: frontend/backend rollback can restore code, but cannot
     automatically restore Supabase schema/data compatibility.
   - Migration rollback or forward-fix procedure remains separate.

6. GitHub default branch advisory count may remain stale until the security
   update branch is merged or default branch is updated.
   - Impact: Dependabot UI can still report vulnerabilities for the default
     branch even though `chore/add-playwright-test` locally audits clean.

## 7. Deployment option decision matrix

Official documentation checked on 2026-08-18 KST:

- Vercel Pricing: https://vercel.com/pricing
- Vercel Hobby Plan: https://vercel.com/docs/plans/hobby
- Vercel Limits: https://vercel.com/docs/limits
- Vercel Instant Rollback: https://vercel.com/docs/instant-rollback
- Vercel Environment Variables: https://vercel.com/docs/environment-variables
- Railway Pricing: https://docs.railway.com/pricing
- Railway Pricing Plans: https://docs.railway.com/pricing/plans
- Railway Healthchecks: https://docs.railway.com/deployments/healthchecks
- Railway Deployment Actions: https://docs.railway.com/deployments/deployment-actions
- Render Deploy for Free: https://render.com/docs/free
- Render Rollbacks: https://render.com/docs/rollbacks
- Render Preview Environments: https://render.com/docs/preview-environments

| Criterion | A. Vercel frontend + Railway backend/PostgreSQL | B. Render-centered deployment | C. Single host Docker Compose |
|---|---|---|---|
| Next.js fit | Strong: Vercel is purpose-built for Next.js; Hobby is free for personal use. | Acceptable via static/web service, but less native for Next.js previews than Vercel. | Depends on manual Node runtime/reverse proxy. |
| FastAPI and SSE/long AI request | Railway web service fits long-running API better than Vercel functions; healthcheck support exists. | Render web service supports FastAPI, but Free web service spins down after idle and cold starts. | Strong control if host remains awake; high ops burden. |
| PostgreSQL persistence | Railway PostgreSQL fits same project/env and variable references. | Render Free Postgres expires after 30 days; paid DB required for stable demo. | Requires self-managed Postgres volume/backups/migrations. |
| Secret management | Vercel env for frontend API base; Railway variables for DB/OpenAI/JWT. | Render env groups/secrets are available, but all services centered there. | Manual `.env`/server secret hygiene. |
| Sleep/cold start | Vercel static/frontend avoids backend sleep; Railway paid Hobby usage model selected over Free. Must still verify Railway app sleep/usage policy in account. | Free web services spin down after 15 minutes and wake around one minute later. | Depends on host; no platform sleep if VPS stays on. |
| Health/readiness | Railway deploy healthcheck can gate traffic but is not continuous monitoring. | Render health/deploy model available, but current repo lacks health route. | Must implement and operate reverse proxy/systemd/monitoring manually. |
| Preview/CI/CD | Vercel Git previews are strong for frontend. Railway PR environments exist but require setup. | Render preview environments require Pro or higher per docs. | Manual preview environments. |
| Revision tracking/rollback | Vercel rollback for frontend; Railway deployment rollback/redeploy for backend. DB migrations separate. | Render rollbacks available for recent build artifacts; DB disks/data not rolled back by deploy rollback. | Git/image rollback possible only if built; DB rollback manual. |
| Expected monthly cost/cap | Vercel Hobby $0 for personal frontend; Railway Hobby $5/month base with included usage and pay-per-resource beyond. OpenAI billed separately. | Free looks cheaper but DB expiration/cold start are poor for public demo; paid Render can exceed Railway baseline. | VPS cost can be fixed but requires maintenance; local Compose is not public deployment. |
| Operations/teardown | Easy stop/remove services; separate frontend/backend blast radius. | Easy dashboard teardown, but Free DB expiration risk. | Manual teardown, backups, firewall, TLS, updates. |
| Decision | Recommended working decision. | Not selected for this U6 baseline. | Not selected for public product demo baseline. |

## 8. Working decision and actual U10 deployment

Original U6 working decision:

- Frontend: Vercel Hobby.
- Backend: Railway Hobby.
- Database: Railway PostgreSQL.
- External AI: existing OpenAI API.

Actual U10 deployment:

- Frontend: Vercel.
- Backend: Render.
- Database: Supabase PostgreSQL.
- External AI: existing OpenAI API key configured in the backend host.

Why Vercel + Railway was originally preferred:

- The frontend is Next.js 16 and has a small environment surface (`NEXT_PUBLIC_API_BASE_URL`), so Vercel is the lowest-friction fit for preview and production frontend deploys.
- The backend is FastAPI with SSE and AI requests up to 360 seconds. Railway is a better fit than trying to force this into Vercel serverless function limits.
- Railway can colocate backend service and PostgreSQL under one project with environment references and deployment actions.
- The monthly baseline is understandable: Vercel Hobby is $0 for personal/non-commercial use; Railway Hobby is $5/month with included resource usage, then usage-based overage. OpenAI cost remains a separate cap.
- Separating frontend and backend matches the actual repository ownership boundary and avoids turning a two-repo project into a monolithic deployment problem.

Why Render is not selected:

- Render Free web services spin down after 15 minutes of no inbound traffic and may take around one minute to wake.
- Render Free Postgres expires after 30 days and has no backup support, so it is weak for a public portfolio demo that needs predictable persistence.
- Render preview environments require Pro or higher per official docs.
- Render remains a viable paid alternative, but it does not beat Vercel+Railway for this two-repo Next.js + FastAPI + PostgreSQL baseline.

Why Render + Supabase was used for U10:

- Railway CLI authentication did not complete in the local environment, blocking
  backend deployment.
- Render backend deployment succeeded without requiring a paid Railway plan.
- Render PostgreSQL required a payment method, so Supabase PostgreSQL was used
  as the external database.
- The resulting topology is acceptable for a short public portfolio demo, but it
  changes the original cost/persistence boundary and should not be represented
  as the original Railway deployment decision.

Why single-host Compose is not selected:

- It would require Dockerfiles, Compose, reverse proxy/TLS, migration procedure, backups, monitoring, server patching, and rollback discipline that are not currently implemented.
- It obscures the frontend/backend repository boundary.
- It is useful later as a local reproducibility artifact, not as the first public deployment topology.

## 9. Cost, privacy, secret, migration, and rollback boundaries

Cost boundary:

- Vercel Hobby: official docs show $0/month for Hobby and personal/non-commercial constraints. Hobby has included usage caps and function duration constraints.
- Railway Hobby: official docs show $5/month base subscription with $5 included resource usage, with CPU/RAM/storage/egress charged by usage beyond included amount.
- Render Free: official docs show web service and Postgres free options, but with spin-down and Free Postgres 30-day expiration.
- Supabase free database was used for U10. Current account limits, pause policy,
  backups, and spend controls must be verified in the Supabase dashboard before
  making an operating-cost or availability claim.
- OpenAI API: not checked or changed in U6. It remains an external metered cost and must have a separate budget cap before public operation.

Privacy boundary:

- Do not put raw question text, profile fields, tokens, DB URL, or API keys into metric labels, public logs, screenshots, or portfolio claims.
- Request/correlation IDs may be used for lifecycle tracing, but they should remain separate from direct user identifiers in observability.

Secret boundary:

- Backend env names required by `.env.example`: `DATABASE_URL`, `PSYCOPG_DATABASE_URL`, `DATA_GO_KR_SERVICE_KEY`, `OPENAI_API_KEY`, `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`.
- Frontend env name required by `.env.example`: `NEXT_PUBLIC_API_BASE_URL`.
- U6 checked names only, not secret values.
- U10 configured `NEXT_PUBLIC_API_BASE_URL` in Vercel production as
  `https://dodam-backend.onrender.com`.
- U10 used Supabase connection strings in Render backend variables. Secret
  values are intentionally not recorded.

Migration boundary:

- Vercel/Railway/Render platform rollback does not roll back database schema or data.
- DB migration rollback must be separately designed and tested. A previous app image can be incompatible with a later migrated DB.
- Before U10, every migration must have apply order, backup/snapshot decision, rollback/forward-fix decision, and smoke checks.

Health boundary:

- Railway healthchecks can prevent a new deployment from becoming active before an HTTP 200 health endpoint.
- Railway official docs state healthchecks are not continuous monitoring after the deployment goes live.
- Therefore a Railway healthcheck is a deploy gate, not uptime monitoring, alerting, or product observability.

Rollback boundary:

- Vercel frontend rollback can restore a previous frontend deployment, but environment and external API/database behavior may differ.
- Railway backend rollback/redeploy can restore prior code/image/config within retention, but not DB migrations or external AI behavior.
- Render rollback similarly does not roll back disks/data.

Public operating period items requiring user confirmation:

- Exact public demo duration.
- OpenAI monthly spend cap and who owns payment.
- Whether real user accounts are allowed or only seeded demo users.
- Whether public signup remains open.
- Retention period for chat/request rows.
- Whether logs can include sanitized request ids and dependency error classes.

## 10. U7 proposed scope, allowed files, acceptance, stop conditions

U7 goal:

- Reuse the existing lifecycle and make duplicate submit, SSE disconnect, process restart, and external AI failure converge to `COMPLETED`, `FAILED`, or explicit retryable state for the user.

Allowed backend files:

- `app/api/ai_request_controller.py`
- `app/api/chat_controller.py`
- `app/services/ai_request_lifecycle_service.py`
- `app/services/chat/chat_service.py`
- `app/repositories/chat_request_repository.py`
- `app/repositories/ai_request_repository.py`
- `app/db/models/recommendation_request.py`
- `app/db/models/eligibility_request.py`
- targeted migrations only if required for idempotency/stale cleanup
- targeted schema/test files for these flows

Allowed frontend files:

- `../front/apis/recommendationApi.js`
- `../front/apis/eligibilityApi.js`
- `../front/apis/chatApi.js`
- `../front/apis/chatStreamClient.js`
- `../front/apis/sseStreamClient.js`
- `../front/app/chat/page.js`
- related focused frontend tests

Acceptance:

- Recommendation and eligibility requests have duplicate-submit protection or an explicit accepted idempotency alternative.
- Recommendation and eligibility `PROCESSING` rows cannot remain forever after process restart.
- Chat, recommendation, and eligibility recovery paths have focused tests.
- SSE disconnect after accepted request either recovers by request id or terminates as a retryable failed state.
- LLM/embedding/DB failures are user-safe and distinguish retryable/non-retryable where applicable.
- Fallback result and AI-enriched result are represented without overstating grounding.

Stop conditions:

- DB migration is required but consuming frontend/API contract impact is unclear.
- Existing frontend dirty changes conflict with the needed U7 edits.
- External API live calls are required to reproduce failures.
- The fix needs a new queue/broker instead of the current request lifecycle.

## 11. Portfolio claims: can say / cannot say yet

Can say now:

- Dodam has a DB-backed chat SSE lifecycle with request IDs, idempotency keys, completed-payload replay, refresh recovery polling, and startup stale cleanup for chat requests.
- Recommendation and eligibility use request-id-based asynchronous POST+GET
  polling with stored status/result JSON, idempotency keys, and startup stale
  processing cleanup.
- The frontend chat flow now sends idempotency keys and guards duplicate
  chat/recommendation/eligibility submits in the UI.
- Recommendation flow stores candidates, assessments, normalized result JSON, and uses deterministic fallback when LLM rerank is unavailable.
- Focused U6 verification passed 72 backend tests and 8 frontend progress tests without external AI calls or DB mutation.
- After the latest frontend hardening/advisory update, local `npm audit`,
  `npm run lint`, `node app/chat/chatProgress.test.mjs`, and `npm run build`
  passed on `chore/add-playwright-test` at `3315f52`.
- Vercel frontend + Railway backend/PostgreSQL was the U6 working deployment decision based on official platform docs checked on 2026-08-18.
- U10 deployed the current demo as Vercel frontend + Render backend + Supabase
  PostgreSQL and verified frontend HTTP 200 plus backend readiness HTTP 200 on
  2026-08-18 KST.

Cannot say yet:

- Cannot say Dodam is production-ready or externally operated by real users.
- Cannot say Railway smoke, rollback, healthcheck, or migration procedures have been verified.
- Cannot say deployed recommendation/eligibility lifecycle has been browser-E2E
  verified, even though local code/test evidence now covers idempotency and
  stale cleanup.
- Cannot say current latency is an SLA.
- Cannot say Render or Railway healthcheck equals continuous monitoring.
- Cannot say platform rollback handles DB migration rollback.
- Cannot say public users have validated the deployed workflow.
- Cannot say deployed auth, policy import/search, OpenAI generation,
  recommendation/chat SSE lifecycle, fallback display, or browser E2E have been
  verified until explicit deployed smoke scenarios are run.

## Verification run in U6

Backend:

```bash
PYTHONPATH=. .venv/bin/pytest -q -p no:cacheprovider \
  tests/test_chat_request_repository.py \
  tests/test_main_lifespan.py \
  tests/test_chat_service.py \
  tests/test_chat_controller.py \
  tests/test_ai_request_controller.py
```

Result: 72 passed, 1 warning.

Frontend:

```bash
node /Users/hb/Documents/kosa-course/projects/mini-2/front/app/chat/chatProgress.test.mjs
```

Result: 8 passed.

Not run:

- No OpenAI paid calls.
- No live retrieval/generation evaluation.
- No DB write/integration smoke.
- No external deployment.
- No browser E2E.

## Verification after U10 deployment

Checked on 2026-08-18 KST after Render, Supabase, and Vercel setup:

```bash
curl -sS -i https://dodam-backend.onrender.com/health/ready
```

Result: HTTP 200 with `database.status = "ok"` and `schema.status = "ok"`.

```bash
curl -sS -I https://dodam-frontend.vercel.app
```

Result: HTTP 200 from Vercel.

U10 deployment artifacts:

- Backend deployed URL: `https://dodam-backend.onrender.com`
- Frontend deployed URL: `https://dodam-frontend.vercel.app`
- Backend deployed/documented commit: `b2318d4`
- Frontend deployed commit: `a2901fc`
- Latest pushed frontend chat hardening/advisory commit:
  `3315f52` on `chore/add-playwright-test`; not yet claimed as the deployed
  Vercel production revision in this document.
- Supabase migration set: `supabase/migrations/`

Latest frontend local verification after chat hardening/advisory update:

```bash
npm audit --json
npm run lint
node app/chat/chatProgress.test.mjs
npm run build
```

Result: audit returned 0 vulnerabilities; lint passed with one existing
`app/layout.js` font warning; chat progress tests passed 8/8; Next.js
production build passed.

Still not verified after U10:

- Browser E2E with user signup/login.
- Policy import/search data freshness.
- OpenAI-backed recommendation/chat generation success.
- SSE disconnect recovery on deployed infrastructure.
- Fallback provenance display in deployed UI.
- Continuous monitoring, alerting, SLA, or real-user operation.
