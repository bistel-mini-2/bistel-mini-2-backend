# Dodam Product Lifecycle Baseline

## 1. Status and audit timestamp

- Audit unit: U6 Product lifecycle baseline and deployment decision only.
- Audit timestamp: 2026-08-18 KST.
- Method: static code and document audit, focused deterministic tests, and official platform documentation check.
- Code changes: none.
- Allowed artifact written: `docs/PRODUCT_LIFECYCLE_BASELINE.md`.
- Out of scope not started: U7 lifecycle fixes, U8 CI gate, U9 Docker/health package, U10 external deployment.

Evidence boundaries:

- Retrieval correctness remains U1-U4 evidence.
- Generated grounding and display cleanliness remain U5 evidence.
- This document covers current product lifecycle implementation, recovery gaps, and deploy topology choice.
- Existing deployment files, local tests, or pricing pages are not proof of deployed production operation.

## 2. Repository revisions and dirty-worktree boundary

Backend repository:

- Path: `/Users/hb/Documents/kosa-course/projects/mini-2/back`
- Branch: `refactor/chat-handler-result-lifecycle`
- HEAD: `89960ee`
- Initial status: clean.

Frontend repository:

- Path: `/Users/hb/Documents/kosa-course/projects/mini-2/front`
- Branch: `develop`
- HEAD: `4d3eb30`
- Initial status: existing user-owned dirty files:
  - `app/chat/page.js`
  - `app/components/ChatPromptDock.js`
  - `app/components/EligibilityCardChat.js`

Boundary:

- Frontend dirty files were read as current implementation evidence and were not overwritten, staged, stashed, reverted, formatted, or cleaned.
- Backend was clean before this U6 artifact. This document is the only intended backend change.

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

Status: partial. Request ID, ownership check on read, DB status/result, timeout-to-failed, and polling exist. Idempotency key, duplicate-submit protection, stale processing recovery, and refresh recovery storage for this dedicated recommendation flow are missing.

### 3.3 Eligibility request path

Current implemented path:

1. Frontend eligibility actions call `front/apis/eligibilityApi.js::createRequest()`, then poll `getResult()`.
2. Backend `POST /api/v1/eligibility/requests` validates optional chat session ownership, creates `eligibility_request`, marks it `PROCESSING`, commits, and schedules `process_ai_condition_request()`.
3. `AiRequestLifecycleService` resolves policy, runs Condition Agent, saves assessment evidence via `PolicyAssessmentRepository`, writes `eligibility_request.result_json`, and marks terminal status.
4. `GET /api/v1/eligibility/requests/{request_id}` verifies user ownership and returns stored result. If linked to a chat session, it persists a terminal eligibility result message once.
5. Frontend polls up to 15 attempts and renders `EligibilityCardChat`.

Status: partial. Request ID, chat-source correlation, owner check, DB result/status, and polling exist. Dedicated eligibility idempotency, duplicate prevention, and process restart stale cleanup are missing.

### 3.4 Recommendation and eligibility SSE path

Current implemented path:

- `POST /api/v1/recommendations/requests/stream` and `POST /api/v1/eligibility/requests/stream` stream progress and terminal result.
- Frontend has generic `postSseStream()`.

Status: partial. These endpoints are useful for progress display, but the request is processed inside the stream task. If the stream is interrupted, there is no explicit accepted request replay contract equivalent to chat SSE. General POST plus GET polling is currently the safer recovery path for recommendation and eligibility.

## 4. Lifecycle status matrix

| Stage | Backend evidence | Frontend evidence | Persisted state | Failure/recovery behavior | Test evidence | Status | Gap and impact |
|---|---|---|---|---|---|---|---|
| User input and auth gate | `CurrentUserDep` in chat/recommendation/eligibility controllers; `ensure_owned_session()` for chat and chat-linked eligibility | `app/chat/page.js` checks authenticated state before chat/recommendation/eligibility actions | User id attached to request/session rows | Unauthenticated UI blocks; backend rejects via dependency | `test_chat_controller.py`, `test_ai_request_controller.py` ownership cases | implemented | CORS is permissive and should be tightened before public deployment. |
| Chat submit/SSE accepted | `send_message_stream()` saves user message and `chat_request`, emits accepted request_id | `sendMessageStream()` handles accepted/requestId and provisional message | `chat_request.status=processing`, user message row | If no accepted request_id, UI cannot recover and shows failed state | `test_send_message_stream_emits_tokens_then_done`, controller SSE test | implemented | Accepted-before-result contract is chat-only. |
| Chat correlation/idempotency | Unique `(chat_session_id, idempotency_key)` and replay of existing completed request | `createIdempotencyKey()`, pending request storage | `chat_request.idempotency_key`, `response_payload_json` | Completed duplicate returns saved payload; processing duplicate emits accepted | `test_send_message_stream_idempotency_completed_returns_saved_payload` | partial | Processing duplicate does not attach to live stream; frontend must poll by request id. |
| Chat graph/service execution | `_run_chat()` branch routing, policy/evidence persistence helpers | Progress, intent, token, card rendering in chat page | `chat_message`, `chat_message_policy`, `chat_message_evidence`, session slot | Graph error marks request failed with user-safe message | chat service/controller tests | partial | Full browser E2E with real backend/DB was not run in U6. |
| Chat DB result save | `mark_completed()` saves assistant_message_id and `response_payload_json` | `applyCompletedChatPayload()` replaces provisional message and clears pending | `chat_request`, message/evidence/policy rows | Persist failure marks failed and does not emit done | `test_send_message_stream_final_persist_failure_does_not_emit_done` | implemented | No deployed DB migration proof. |
| Chat SSE disconnect recovery | `CancelledError` path continues or schedules disconnect recovery; stale startup cleanup | Polls request status after incomplete stream and on refresh | Existing request remains `processing` until completed/failed/stale | Refresh can recover completed payload or show failed/cancelled | frontend recovery code inspected; backend unit coverage for status endpoints | partial | In-process recovery depends on process survival; restart only fails stale request after cutoff, not resume. |
| Recommendation create/poll | `POST /recommendations/requests`, background task, `GET /requests/{id}` reads only stored result | `createRecommendationRequest()`, `getRecommendationResult()`, 15x polling | `recommendation_request` with parsed/merged/result JSON and status | Timeout/background exception marks failed; UI maps failed to error | focused backend tests and chat recommendation branch tests | partial | No idempotency, no stale cleanup, no refresh recovery for dedicated recommendation flow. |
| Recommendation graph and fallback | `RecommendationGraphRunner`, normalizer, rerank fallback service | Recommendation cards consume normalized result | `recommendation_candidate`, `policy_assessment`, `recommendation_request.result_json` | LLM rerank failure falls back to rule/assessment result | retrieval and recommendation tests exist; not all run in U6 | partial | Fallback is not exposed as a first-class user-visible AI-vs-deterministic provenance flag. |
| Eligibility create/poll | `POST /eligibility/requests`, `GET /eligibility/requests/{id}`, chat source ref ownership | `eligibilityApi.createRequest()`, `fetchEligibilityResult()` | `eligibility_request`, `policy_assessment`, optional chat assistant message | Failed status masks internal error; polling stops on terminal | `test_ai_request_controller.py` eligibility cases | partial | No eligibility idempotency/stale cleanup; polling state is component-local. |
| Failure masking | `AI_REQUEST_USER_ERROR_MESSAGE`, `_safe_error_message()`, chat retryable status | User-facing Korean error messages and retry UI | error message stored on request rows | Internal error details not returned for failed eligibility | `test_eligibility_result_response_masks_error_message` | implemented | Logs may still contain exceptions; U11 should define privacy-safe logging. |
| Process restart stale recovery | Startup marks stale `chat_request` processing rows failed | Refresh polling then sees failed/retryable chat request | `chat_request` only | Old chat processing rows become failed after 5 minutes | `test_main_lifespan.py`, `test_chat_request_repository.py` | partial | Recommendation/eligibility request tables have no equivalent stale processing cleanup. |
| Timeout/retry | AI background timeout 360s; DB statement timeout 60s; chat failed requests retryable unless invalid input | Frontend recovery max wait 45s, retry creates new idempotency key if recovery fails | Failed request rows | Retry is mostly UI-level new request; not provider-level retry policy | focused tests | partial | Retry taxonomy for LLM/embedding/DB failures is not explicit. |
| Frontend result display | Backend response schemas include policies/evidences/cards | `AssistantMessage`, `EligibilityCardChat`, recommendation card components | Chat messages and request result JSON | Failed/provisional states shown with retry | frontend progress tests only | partial | Visual/browser E2E was not run; current dirty UI files are user-owned. |
| Backend/frontend CI | GitHub workflows exist only for PR title/branch/linked issue/Discord/issue close | Same | None | No deterministic build/test gate in workflow | Local tests only | missing | U8 must add release-quality CI; current workflows do not prove app correctness. |
| Build/deploy package | No backend Dockerfile/Compose/provider manifest found; frontend has Next.js package scripts and no `vercel.json` | Next.js `build/start`; `NEXT_PUBLIC_API_BASE_URL` env example | None | No health/readiness route found | Not run | missing | U9 must add deployment package and health/readiness before deploy-ready claim. |

## 5. Existing strengths

- Chat SSE has the best current product lifecycle baseline: accepted request id, DB-backed status, idempotency key, completed-payload replay, retryable failure state, frontend pending request storage, and refresh recovery.
- Recommendation and eligibility use `request_id` and DB status/result tables, so polling after normal POST is already safer than purely synchronous generation.
- Backend separates `RequestStatus`, user-facing statuses, assessment state, and frontend loading variants in docs and schemas.
- Chat evidence and policy links are normalized into dedicated rows, reducing reliance on opaque `structured_json`.
- Focused deterministic tests cover many lifecycle edges without external AI calls.
- Secrets are represented through env names only; U6 did not print or store secret values.

## 6. Productization gaps ordered by severity

1. Recommendation and eligibility do not have stale processing cleanup.
   - Impact: process crash after marking `PROCESSING` can leave user-visible requests loading forever.
   - U7 blocker because product lifecycle requires finite terminal states.

2. Recommendation and eligibility lack idempotency/duplicate-submit protection.
   - Impact: double click, refresh/retry, or flaky network can create duplicate AI requests and duplicate cost.
   - U7 should add a request-level idempotency contract or an equivalent duplicate guard.

3. No deployment health/readiness endpoint or backend deploy package exists.
   - Impact: Railway healthcheck and rollback cannot be verified; U10 external deploy would be premature.
   - U9 blocker.

4. CI workflows do not run app tests/builds.
   - Impact: PRs can pass governance checks while breaking lifecycle behavior.
   - U8 blocker.

5. Recommendation/eligibility SSE endpoints are not as recoverable as chat SSE.
   - Impact: progress stream disconnect can produce ambiguous user state unless the app uses POST+GET polling.
   - U7 should either align SSE accepted/replay semantics or prefer polling for these flows.

6. Fallback provenance is not a first-class display contract across all flows.
   - Impact: deterministic fallback, LLM rerank success, and degraded output can be blurred in portfolio/product claims.
   - U7/U11 should expose safe provenance without leaking prompts or raw user data.

7. Public deployment privacy and observability are not defined.
   - Impact: request correlation exists, but metric labels/log boundaries are not yet enforced.
   - U11 blocker.

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

## 8. Working decision: Vercel + Railway

Working decision:

- Frontend: Vercel Hobby.
- Backend: Railway Hobby.
- Database: Railway PostgreSQL.
- External AI: existing OpenAI API.

Why this is preferred:

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

Why single-host Compose is not selected:

- It would require Dockerfiles, Compose, reverse proxy/TLS, migration procedure, backups, monitoring, server patching, and rollback discipline that are not currently implemented.
- It obscures the frontend/backend repository boundary.
- It is useful later as a local reproducibility artifact, not as the first public deployment topology.

## 9. Cost, privacy, secret, migration, and rollback boundaries

Cost boundary:

- Vercel Hobby: official docs show $0/month for Hobby and personal/non-commercial constraints. Hobby has included usage caps and function duration constraints.
- Railway Hobby: official docs show $5/month base subscription with $5 included resource usage, with CPU/RAM/storage/egress charged by usage beyond included amount.
- Render Free: official docs show web service and Postgres free options, but with spin-down and Free Postgres 30-day expiration.
- OpenAI API: not checked or changed in U6. It remains an external metered cost and must have a separate budget cap before public operation.

Privacy boundary:

- Do not put raw question text, profile fields, tokens, DB URL, or API keys into metric labels, public logs, screenshots, or portfolio claims.
- Request/correlation IDs may be used for lifecycle tracing, but they should remain separate from direct user identifiers in observability.

Secret boundary:

- Backend env names required by `.env.example`: `DATABASE_URL`, `PSYCOPG_DATABASE_URL`, `DATA_GO_KR_SERVICE_KEY`, `OPENAI_API_KEY`, `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`.
- Frontend env name required by `.env.example`: `NEXT_PUBLIC_API_BASE_URL`.
- U6 checked names only, not secret values.

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
- Recommendation and eligibility use request-id-based asynchronous POST+GET polling with stored status and result JSON.
- Recommendation flow stores candidates, assessments, normalized result JSON, and uses deterministic fallback when LLM rerank is unavailable.
- Focused U6 verification passed 72 backend tests and 8 frontend progress tests without external AI calls or DB mutation.
- Vercel frontend + Railway backend/PostgreSQL is the current working deployment decision based on official platform docs checked on 2026-08-18.

Cannot say yet:

- Cannot say Dodam is deployed, production-ready, or externally operated.
- Cannot say Vercel/Railway smoke, rollback, healthcheck, or migration procedures have been verified.
- Cannot say recommendation/eligibility lifecycle is fully restart-safe or duplicate-safe.
- Cannot say current latency is an SLA.
- Cannot say Railway healthcheck equals continuous monitoring.
- Cannot say platform rollback handles DB migration rollback.
- Cannot say public users have validated the deployed workflow.

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
