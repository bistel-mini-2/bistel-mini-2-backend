# Issue #119 — 챗봇 대화 맥락 슬롯 구현 계획

> 브랜치: `feature/119-chatbot-context-slot`
> 관련 스펙: `docs/FEATURE_SPEC.md §3.8`
> 의존: PR #134 (apply intent → Apply Preparation API, 머지 완료)

---

## 1. 배경

현재 챗봇은 `chat_service.py:143-151`에서 최근 5개 메시지를 텍스트로 추려 supervisor LLM에 넘김. 사용자가 "그 정책", "방금 그거" 같은 지시어를 쓰면 LLM이 텍스트 추론으로 정책을 식별해야 하므로 정확도가 불안정하고, 정책 ID를 모르기 때문에 Apply/Eligibility branch는 매번 RAG를 다시 돌려서 slug를 추출해야 함.

**슬롯 = 세션 대화의 메타데이터 캐시.** "직전 거론 정책 = policy_id=42 / slug=child-care"를 구조화된 형태로 보관해서, 다음 메시지에서 추론 없이 코드가 직접 참조하고 RAG도 스킵.

## 2. 완료 조건 및 범위

### 이슈 본문의 완료 조건

1. ✅ "아이돌봄서비스 어떻게 신청해?" 응답 직후 "신청 기간은?" 메시지에 같은 정책 슬롯이 자동 추출됨
2. ⚠️ 슬롯이 있을 때 Apply / Eligibility branch가 추가 RAG 검색 없이 바로 해당 정책으로 API 호출
3. ✅ 단위 테스트 — 슬롯 갱신·조회·해소

### 이번 PR 범위 한정

- **Apply branch만 슬롯 기반 RAG 스킵 적용.** Eligibility branch는 호출할 평가 API가 미구현 상태(PR #126 "결과 조회"는 트리거 아님). 슬롯 인프라 자체는 동일하게 사용하되 Eligibility는 RAG 스킵까지만 적용, API 호출은 후속 이슈.
- TTL / 동시 요청 락 / stale ID validation 등은 **이번 PR에서 제외** (§9 참고).

## 3. 슬롯 데이터 구조

`chat_session.slot_json` (JSONB, NOT NULL DEFAULT `'{}'`)

```json
{
  "recent_policies": [
    {
      "policy_id": 42,
      "slug": "child-care",
      "policy_name": "아이돌봄서비스",
      "last_action": "APPLY_TARGET"
    }
  ],
  "updated_at": "2026-06-23T14:00:00Z"
}
```

- `recent_policies`: 최대 3개. 새 정책 prepend, 중복 slug 제거.
- `updated_at`: 디버깅/로깅용. **TTL 가드 없음**(범용 챗봇 컨벤션, ChatGPT 동일).
- `last_action`: `RECOMMENDED | COMPARED | ELIGIBILITY_TARGET | APPLY_TARGET` (기존 `chat_message_policy.action_type` enum 재사용).

## 4. 작업 항목

### 4.1 DB 마이그레이션
**파일 신규**: `db/migrations/119_chat_session_slot_json.sql`

```sql
ALTER TABLE chat_session
  ADD COLUMN slot_json JSONB NOT NULL DEFAULT '{}'::jsonb;
```

### 4.2 ORM 모델 동기화
**파일 수정**: `app/db/models/chat_session.py`

```python
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

slot_json: Mapped[dict] = mapped_column(
    JSONB,
    nullable=False,
    server_default=text("'{}'::jsonb"),
)
```

### 4.3 State 타입 확장
**파일 수정**: `app/ai/states/chat_state.py`

```python
class SlotPolicy(TypedDict):
    policy_id: int
    slug: str
    policy_name: str
    last_action: str

class ChatSlot(TypedDict):
    recent_policies: list[SlotPolicy]
    updated_at: NotRequired[str]

class SupervisorDecision(TypedDict):
    intent: Intent
    raw: str
    resolved_policy_slug: NotRequired[str | None]  # 신규

class ChatGraphState(TypedDict):
    ...
    slot: NotRequired[ChatSlot]  # 신규 — supervisor 입력용
```

### 4.4 Supervisor 노드 변경
**파일 수정**: `app/ai/nodes/chat/chat_nodes.py:305-323`

- `SUPERVISOR_SYSTEM` 프롬프트에 슬롯 정보 주입 (시스템 메시지에 박는 방식, §1번 결론).
- `_IntentDecision` Pydantic 모델에 `resolved_policy_slug: str | None` 필드 추가.
- LLM에게 *"사용자가 지시어로 슬롯 정책을 가리키면 그 slug 반환, 새 정책 명시면 null"* 지시.

```python
SUPERVISOR_SYSTEM = """당신은 임신·출산·육아 정책 챗봇의 Supervisor입니다.
... (기존 intent 분류 지침)

[직전 거론 정책]
{slot_context}

사용자가 "그 정책", "거기", "방금 그거" 등으로 직전 정책을 가리키는 것으로 보이면
resolved_policy_slug에 해당 slug를 반환하세요. 새 정책을 명시했거나 슬롯 정보가
비어있으면 null을 반환하세요.
"""

def _format_slot_context(slot: ChatSlot | None) -> str:
    if not slot or not slot.get("recent_policies"):
        return "(없음)"
    return "\n".join(
        f"- {p['policy_name']} (slug={p['slug']}, action={p['last_action']})"
        for p in slot["recent_policies"]
    )
```

### 4.5 Apply Branch — 슬롯 기반 RAG 스킵
**파일 수정**: `app/ai/nodes/chat/chat_nodes.py:353-398`

```python
async def branch_apply(self, state: ChatGraphState) -> ChatGraphState:
    decision = state.get("supervisor_decision") or {}
    resolved_slug = decision.get("resolved_policy_slug")

    if resolved_slug:
        # 슬롯 hit — RAG 스킵
        slug = resolved_slug
        policy_name = _find_policy_name_in_slot(state.get("slot"), slug)
        evidences = []  # 트레이드오프: evidence 비움 (PR description에 명시)
        logger.info("chat_slot_resolved", extra={"intent": "apply", "slot_used": True})
    else:
        # 기존 경로 — RAG로 slug 추출
        policies, evidences = await self._rag_lookup(state["user_content"])
        slug, policy_name = _pick_apply_target(policies)
        logger.info("chat_slot_resolved", extra={"intent": "apply", "slot_used": False})

    if slug is None:
        # 기존 fallback 유지
        ...
```

**버그 동시 수정**: `chat_nodes.py:381-390`의 `"policy_id": slug` → `branch_policies`의 `policy_id` 필드 자체를 제거하거나 명시적으로 None. 슬롯 도입 후 정수 ID와 문자열 slug가 섞이면 위험.

### 4.6 Eligibility Branch — RAG 스킵만 적용
**파일 수정**: `app/ai/nodes/chat/chat_nodes.py:347-348`

```python
async def branch_eligibility(self, state: ChatGraphState) -> ChatGraphState:
    decision = state.get("supervisor_decision") or {}
    resolved_slug = decision.get("resolved_policy_slug")
    if resolved_slug:
        # 슬롯 정책으로 답변 생성. 평가 API 미구현이므로 RAG 스킵 후 안내 답변만.
        return await self._branch_with_slot("eligibility", state, resolved_slug)
    return await self._branch_with_rag("eligibility", state)
```

### 4.7 Service — 슬롯 갱신 위치
**파일 수정**: `app/services/chat_service.py:190-240` (`_persist_assistant_outputs`)

`bulk_save_message_policies` 호출 직후, `slug_to_policy_id` 매핑이 손에 있는 시점에 슬롯 업데이트:

```python
slug_to_policy_id = await _resolve_policy_ids(db, policy_links_to_save)
await ChatRepository.bulk_save_message_policies(...)

# 슬롯 갱신 — graph 외부에서 수행
new_slot = _build_slot(
    current_slot=session.slot_json,
    policy_links=policy_links_to_save,
    slug_to_policy_id=slug_to_policy_id,
    branch_policies=graph_result.get("assistant_payload", {}).get("policies", []),
)
await ChatRepository.update_session_slot(db, session_id, new_slot)
```

`_build_slot`: 새 정책을 list 앞에 prepend, 중복 slug 제거, 최대 3개로 cap.

**파일 수정**: `app/repositories/chat_repository.py` — `update_session_slot(session_id, slot_dict)` 메서드 추가.

**파일 수정**: `app/services/chat_service.py:91` — `send_message`에서 session 객체를 load할 때 slot도 함께 가져와 graph state에 주입:

```python
graph_result = await _run_supervisor_graph(
    user_id=user_id,
    user_content=content,
    history=history,
    slot=session.slot_json,  # 신규
)
```

### 4.8 Graph state 전달
**파일 수정**: `app/services/chat_service.py:171-187` (`_run_supervisor_graph`)

`slot` 인자를 받아 initial state에 포함.

### 4.9 로깅 1줄
slot hit/miss 트래킹용 (§4번 결론):

```python
logger.info(
    "chat_slot_resolved",
    extra={
        "session_id": session_id,
        "intent": intent,
        "slot_used": bool(resolved_slug),
        "rag_skipped": bool(resolved_slug),
    },
)
```

## 5. 테스트

**파일 신규**: `tests/test_chat_slot.py`

- `test_slot_first_message_empty()`: 첫 메시지 후 슬롯이 정책으로 갱신되는지
- `test_slot_resolves_demonstrative()`: "그 정책 신청 기간은?" 메시지가 슬롯 정책을 가리키는지 (supervisor mock)
- `test_slot_new_policy_resets()`: 새 정책 명시 시 supervisor가 `resolved_policy_slug=None` 반환
- `test_slot_cap_three_policies()`: list가 3개를 넘지 않는지
- `test_slot_dedup_same_slug()`: 같은 slug는 한 번만 들어가는지
- `test_apply_branch_skips_rag_with_slot()`: 슬롯 있을 때 RAG 호출 안 되는지 (FakeRagService 호출 카운트)
- `test_apply_branch_evidence_empty_with_slot()`: 슬롯 경로 시 evidences가 빈 채 저장되는지 (의도된 동작)

**파일 수정**: 기존 `tests/test_chat_branch_apply.py`, `tests/test_chat_service.py` — 슬롯 인자 추가에 따른 fixture 업데이트.

## 6. PR Description 초안

```markdown
## 개요
챗봇 세션에 직전 거론 정책을 보관하는 `slot_json` 추가. 지시어("그 정책",
"방금 그거") 해소를 LLM 텍스트 추론에서 결정론적 ID 참조로 전환.

## 변경 사항
- `chat_session.slot_json` JSONB 컬럼 추가 (NOT NULL DEFAULT `{}`)
- Supervisor가 슬롯을 보고 `resolved_policy_slug` 반환
- Apply branch: 슬롯 hit 시 RAG 스킵, ApplyPreparationService 바로 호출
- Eligibility branch: 슬롯 hit 시 RAG 스킵 (평가 API 미연결, 답변만 슬롯 정책 기반)
- 슬롯 갱신은 `_persist_assistant_outputs`에서 slug→id 매핑 직후 수행
- `branch_apply`의 `policy_id` 필드에 slug가 들어가던 버그 동시 수정

## 범위 한정 (리뷰 시 참고)
- Eligibility branch는 평가 API가 미구현이라 RAG 스킵까지만 적용.
  슬롯 기반 분석 트리거는 후속 이슈로 분리.
- Apply intent에서 슬롯 경로로 응답 시 `chat_message_evidence`는 비어 저장.
  "추가 RAG 검색 없이" 요구사항과의 트레이드오프.
- TTL / 동시 요청 락 / stale ID validation 등은 운영 데이터 수집 후 결정.

## 테스트
- `tests/test_chat_slot.py` 신규 (슬롯 갱신·조회·해소·cap·dedup)
- 기존 chat 테스트에 슬롯 fixture 추가

## 의존
- PR #134 (apply intent) 머지 완료
```

## 7. 제외 사항 (왜 안 하는지)

### TTL / 슬롯 만료
- 범용 챗봇(ChatGPT, Claude.ai)은 세션 단위로 컨텍스트 유지, TTL 없음. 사용자가 새 컨텍스트 원하면 새 세션 생성.
- 임계값(30분? 1시간?) 결정 근거가 데이터 0인 상태에서 자의적.
- 운영 후 *"옛 슬롯이 잘못 잡히는 빈도"*가 측정되면 그때 도입.

### 동시 요청 시 덮어쓰기 락
- 챗봇 UX상 사용자는 응답 받고 다음 메시지 보냄. 멀티 디바이스 동시 사용은 엣지 케이스.
- 필요해지면 `last_message_at` 기반 조건부 UPDATE 한 줄로 대응 가능 (코드 변경 작음).

### Stale `policy_id` validation
- 슬롯의 정책이 그 사이 비활성화될 가능성. 정책 삭제 빈도가 낮아 첫 버전은 무시.
- 운영 데이터로 빈도 측정 후 결정.

### `chat_message_policy` 동적 추출 방식
- 이슈 본문이 제시한 대안이지만, history 로딩 쿼리 확장이 필요하고 매 메시지마다 join 비용 발생.
- `slot_json` 캐시 방식이 더 빠르고 단순.

## 8. 체크리스트 (다음 세션에서 그대로 사용)

- [ ] `db/migrations/119_chat_session_slot_json.sql` 생성
- [ ] `app/db/models/chat_session.py`에 `slot_json` Mapped 컬럼 추가
- [ ] `app/ai/states/chat_state.py`에 `SlotPolicy`, `ChatSlot`, `slot` 필드, `resolved_policy_slug` 추가
- [ ] `app/ai/nodes/chat/chat_nodes.py` supervisor 프롬프트에 슬롯 주입 + `_IntentDecision` 확장
- [ ] `app/ai/nodes/chat/chat_nodes.py` `branch_apply` 슬롯 분기 + `policy_id` 버그 수정
- [ ] `app/ai/nodes/chat/chat_nodes.py` `branch_eligibility` 슬롯 분기
- [ ] `app/repositories/chat_repository.py` `update_session_slot` 메서드 추가
- [ ] `app/services/chat_service.py` `_persist_assistant_outputs`에 슬롯 갱신 + `send_message`에서 slot graph 전달
- [ ] 슬롯 hit/miss 로깅 추가
- [ ] `tests/test_chat_slot.py` 신규 (7개 케이스)
- [ ] 기존 `tests/test_chat_*.py` fixture 업데이트
- [ ] PR description에 §6 내용 포함, Eligibility 범위 한정 명시
