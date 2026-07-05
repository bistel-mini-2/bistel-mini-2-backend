# Chat Request Lifecycle Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make PR 270's chat request lifecycle safe for PostgreSQL and ensure stale requests are cleaned during application startup without runtime DDL.

**Architecture:** Keep the existing `TIMESTAMP WITHOUT TIME ZONE` schema and centralize UTC-naive timestamp creation in the repository. Treat SQL migrations as the only schema-management boundary, and invoke stale-request cleanup once from the FastAPI lifespan using a dedicated SQLAlchemy session.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async ORM, PostgreSQL/asyncpg, pytest

---

### Task 1: Lock the timestamp and migration boundaries with tests

**Files:**
- Modify: `tests/test_chat_service.py`
- Create: `tests/test_chat_request_repository.py`

- [ ] **Step 1: Add repository tests for UTC-naive timestamps**

Create requests with `processing` status, call `mark_completed`, `mark_failed`, and `mark_cancelled` using an `AsyncMock` session, and assert `completed_at.tzinfo is None` for every result.

- [ ] **Step 2: Add a stale-cutoff binding test**

Capture the parameters passed to `db.execute()` by `mark_stale_processing_failed()` and assert `params["cutoff"].tzinfo is None`.

- [ ] **Step 3: Add a no-runtime-DDL test**

Patch `ChatRequestRepository.ensure_schema` with an `AsyncMock`, exercise create and lookup methods, and assert that it was not awaited.

- [ ] **Step 4: Run the new tests and verify they fail**

Run: `PYTHONPATH=. /Users/hb/Documents/mini-2/back/.venv/bin/pytest -q tests/test_chat_request_repository.py`

Expected: failures showing timezone-aware values and runtime `ensure_schema()` calls.

### Task 2: Make repository persistence PostgreSQL-safe

**Files:**
- Modify: `app/repositories/chat_request_repository.py`
- Test: `tests/test_chat_request_repository.py`

- [ ] **Step 1: Add one timestamp helper**

Add `_utc_now_naive()` returning `datetime.now(timezone.utc).replace(tzinfo=None)` and use it for completion, failure, cancellation, and stale cutoff timestamps.

- [ ] **Step 2: Remove runtime schema mutation**

Delete `ensure_schema()` and all calls to it. Keep `db/migrations/269_chat_request_lifecycle.sql` as the schema source of truth.

- [ ] **Step 3: Run repository tests**

Run: `PYTHONPATH=. /Users/hb/Documents/mini-2/back/.venv/bin/pytest -q tests/test_chat_request_repository.py`

Expected: all tests pass.

### Task 3: Run stale cleanup during startup

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_main_lifespan.py`

- [ ] **Step 1: Add a failing lifespan test**

Patch `psycopg_pool`, `PolicyRepository.ensure_search_indexes`, `AsyncSessionLocal`, and `ChatRequestRepository.mark_stale_processing_failed`. Enter the lifespan context and assert cleanup is awaited once with the dedicated session and the session commits once.

- [ ] **Step 2: Implement startup cleanup**

After search-index initialization, open `AsyncSessionLocal()` and call `ChatRequestRepository.mark_stale_processing_failed(db)`, then `await db.commit()`. Allow failures to abort startup so migration or database errors are visible.

- [ ] **Step 3: Run the lifespan test**

Run: `PYTHONPATH=. /Users/hb/Documents/mini-2/back/.venv/bin/pytest -q tests/test_main_lifespan.py`

Expected: pass.

### Task 4: Regression verification

**Files:**
- Verify: `app/repositories/chat_request_repository.py`
- Verify: `app/main.py`
- Verify: `tests/test_chat_request_repository.py`
- Verify: `tests/test_main_lifespan.py`

- [ ] **Step 1: Run focused chat tests**

Run: `PYTHONPATH=. /Users/hb/Documents/mini-2/back/.venv/bin/pytest -q tests/test_chat_request_repository.py tests/test_main_lifespan.py tests/test_chat_service.py tests/test_chat_controller.py`

Expected: all tests pass.

- [ ] **Step 2: Run static diff checks**

Run: `git diff --check`

Expected: no output.

- [ ] **Step 3: Commit the verified change**

Stage the design, plan, repository, startup, and test files, then create one focused commit titled `fix: harden chat request lifecycle persistence`.
