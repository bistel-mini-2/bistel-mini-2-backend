# AI Service Architecture Visual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate an editable SVG and a visually matching PNG that explain the complete mini-2 AI data-build, runtime orchestration, LangGraph, RAG, persistence, and response flow.

**Architecture:** A single deterministic Python generator owns layout constants, palette, labels, connectors, SVG serialization, and Pillow rendering. Both outputs are produced from the same node and edge definitions so their content cannot drift.

**Tech Stack:** Python 3, standard-library XML/string helpers, Pillow, Apple SD Gothic Neo, SVG 1.1

---

### Task 1: Deterministic diagram generator

**Files:**
- Create: `scripts/generate_ai_service_architecture.py`
- Create: `output/architecture/ai-service-architecture.svg`
- Create: `output/architecture/ai-service-architecture.png`

- [x] **Step 1: Define the shared visual model**

Define the 2400×1500 canvas, warm palette, font paths, rounded section/card helpers, node metadata, and edge metadata in `scripts/generate_ai_service_architecture.py`. The node model must carry `id`, `box`, `title`, `subtitle`, `items`, `fill`, and `accent`. The edge model must carry `source`, `target`, `label`, `kind`, and optional explicit points.

- [x] **Step 2: Encode the approved architecture**

Add these groups and no substitute names: data ingestion, PostgreSQL + pgvector, user/Next.js entry points, FastAPI service boundary, ChatService/Intent Classifier/Handler Router, profile/slot/follow-up state, Recommendation Graph, Eligibility Graph, Comparison Graph, Policy Summary Graph, shared RAG/rule/LLM capabilities, OpenAI models, persistence, SSE/Polling response.

- [x] **Step 3: Render SVG**

Implement SVG helpers for rounded rectangles, text, pills, arrow markers, dashed offline edges, solid runtime edges, and thick persistence edges. Write UTF-8 SVG to `output/architecture/ai-service-architecture.svg`.

- [x] **Step 4: Render PNG**

Use Pillow with `/System/Library/Fonts/AppleSDGothicNeo.ttc` for Korean text. Draw the same sections, cards, labels, and arrow routes to `output/architecture/ai-service-architecture.png` at 2400×1500.

- [x] **Step 5: Run the generator**

Run:

```bash
/Users/hb/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/generate_ai_service_architecture.py
```

Expected: both output files are created and the command prints their absolute paths.

### Task 2: Structural and visual verification

**Files:**
- Verify: `output/architecture/ai-service-architecture.svg`
- Verify: `output/architecture/ai-service-architecture.png`

- [x] **Step 1: Verify file structure and required labels**

Run:

```bash
python3 -c "from pathlib import Path; s=Path('output/architecture/ai-service-architecture.svg').read_text(); required=['ChatService','Handler Router','Recommendation Graph','Eligibility Graph','Comparison Graph','Policy Summary Graph','PostgreSQL + pgvector','gpt-5.4-mini','text-embedding-3-large']; assert all(x in s for x in required); print('labels-ok')"
```

Expected: `labels-ok`.

- [x] **Step 2: Verify output dimensions**

Run:

```bash
/Users/hb/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 -c "from PIL import Image; im=Image.open('output/architecture/ai-service-architecture.png'); assert im.size == (2400, 1500); print(im.size)"
```

Expected: `(2400, 1500)`.

- [x] **Step 3: Inspect the PNG visually**

Open the PNG with the local image viewer tool and check Korean glyph rendering, clipped text, arrow crossings, section hierarchy, and warm palette balance. Fix the generator and regenerate if any check fails.

- [x] **Step 4: Check repository diff**

Run:

```bash
git status --short
git diff --check
```

Expected: only the plan, generator, SVG, and PNG are new or modified; the pre-existing `tests/eval/live_eval_results.json` remains untouched.
