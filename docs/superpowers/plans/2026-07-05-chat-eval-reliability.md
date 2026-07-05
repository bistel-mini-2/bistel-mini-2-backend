# Chat Evaluation Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make live chat evaluation scores fail whenever any observable scenario expectation is missing or wrong.

**Architecture:** Extend the live evaluator result with an explicit clarification signal derived from orchestration state. Compute independent exact-match metrics, then require primary intent, secondary intents, response type, clarification, and profile fields to pass for strict E2E success; keep handler accuracy explicitly unmeasured rather than inferred.

**Tech Stack:** Python 3.12, pytest, existing chat evaluation runner

---

### Task 1: Define evaluator regression cases

**Files:**
- Create: `tests/eval/test_live_eval_metrics.py`

- [ ] **Step 1: Add metric fixtures**

Create compact scenario/result builders covering primary intent, secondary intents, response type, clarification, profile changes, and errors.

- [ ] **Step 2: Add strict failure tests**

Assert that a missing or extra secondary intent, a plain text response without clarification state, a profile mismatch, and an errored run each reduce `strict_e2e_success_rate_pct`.

- [ ] **Step 3: Add strict success tests**

Assert that exact secondary intents, explicit clarification state, matching response type, and matching profile fields produce a strict E2E success.

- [ ] **Step 4: Run tests to confirm failure**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/eval/test_live_eval_metrics.py`

Expected: failures because strict metrics and clarification output do not exist yet.

### Task 2: Implement observable strict scoring

**Files:**
- Modify: `tests/eval/live_eval.py:190-342`
- Test: `tests/eval/test_live_eval_metrics.py`

- [ ] **Step 1: Capture clarification state**

Return `actual_clarification` from `_run_scenario_once()` when `pending.kind == "clarification"` or the payload contains `policy_selection`, `slot_request`, or `profile_confirm`.

- [ ] **Step 2: Compare all observable dimensions**

Use exact set equality for secondary intents, boolean equality for clarification, and per-scenario all-field equality for expected profile changes.

- [ ] **Step 3: Compute strict E2E over total runs**

Count a run only when every required observable dimension matches. Keep errors in the denominator and expose `handler_accuracy_pct=None` with a reason explaining that handlers are not directly observable.

- [ ] **Step 4: Update console output**

Print secondary intent accuracy and strict E2E success, and label handler accuracy as unmeasured.

- [ ] **Step 5: Run evaluator metric tests**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/eval/test_live_eval_metrics.py`

Expected: all tests pass.

### Task 3: Preserve explicit clarification and isolate comparison

**Files:**
- Modify: `app/services/chat/handlers/_handler_apply.py:39-52`
- Modify: `tests/eval/live_eval.py:105-180`
- Modify: `tests/eval/test_live_eval_metrics.py`
- Test: `tests/eval/test_mock_scenarios.py`

- [ ] **Step 1: Add failing apply clarification coverage**

Exercise the apply handler without a resolvable policy and assert the returned state contains `pending={"intent": "apply", "kind": "clarification"}`.

- [ ] **Step 2: Preserve clarification state**

Add the pending marker to the unresolved apply branch and keep it through fallback normalization without changing the external payload schema.

- [ ] **Step 3: Add failing comparison stub coverage**

Patch lifecycle runners, assert `chat_handlers._run_comparison_branch` is replaced, restore runners, and assert the original callable returns.

- [ ] **Step 4: Patch the actual comparison call site**

Store, replace, and restore `chat_handlers._run_comparison_branch` instead of the unused `_lifecycle_runners.run_comparison_branch` reference.

- [ ] **Step 5: Run focused tests**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/eval/test_live_eval_metrics.py tests/eval/test_mock_scenarios.py`

Expected: all tests pass.

### Task 4: Verify the evaluation suite

**Files:**
- Verify: `tests/eval/live_eval.py`
- Verify: `tests/eval/test_live_eval_metrics.py`
- Verify: `tests/eval/test_mock_scenarios.py`

- [ ] **Step 1: Run all evaluation tests**

Run: `PYTHONPATH=. .venv/bin/pytest -q tests/eval`

Expected: all tests pass.

- [ ] **Step 2: Run the full suite**

Run: `PYTHONPATH=. .venv/bin/pytest -q`

Expected: all tests pass with the existing single skipped test.

- [ ] **Step 3: Run static checks**

Run: `PYTHONPATH=. .venv/bin/python -m compileall -q tests/eval && git diff --check`

Expected: no errors or whitespace findings.

- [ ] **Step 4: Run live scenarios once**

Load `OPENAI_API_KEY` from `.env` without printing it, then run `PYTHONPATH=. .venv/bin/python tests/eval/live_eval.py --runs 1`.

Expected: 15 runs complete, comparison scenarios use the stub response, and L13 is recognized as clarification.
