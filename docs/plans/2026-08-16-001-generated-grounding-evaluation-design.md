# Generated Grounding Evaluation Design

Status: design only
Date: 2026-08-16
Scope: Dodam policy summary generation grounding evaluation

## Goal

검색 성능 평가는 retrieved evidence correctness를 검증한다. 하지만 정책 요약 화면과 채팅 응답에서 사용자에게 보이는 문장은 LLM 또는 fallback generator가 다시 생성하므로, 별도의 generated answer grounding 평가가 필요하다.

이번 U5의 목적은 구현 전에 최소 평가 계약을 고르는 것이다. API, schema, agent, frontend는 이 문서에서 수정하지 않는다.

## Current Flow Observed

정책 요약 그래프는 정책 상세 정보를 받아 `summary_evidence_search`에서 정책 ID로 evidence chunk를 검색하고, `policy_summary`에서 generator에 `policy`와 `evidence_chunks`를 넘긴다.

- `app/ai/graphs/policy_summary_graph.py`: `search_policy_chunks(query, policy_ids=[policy["policy_id"]], top_k=5)` 호출 후 `summary`, `evidence`, `evidence_chunks`를 반환한다.
- `app/ai/agents/policy_summary_agent.py`: OpenAI API key가 있으면 `ChatOpenAI(model="gpt-5.4-mini", temperature=0)` structured output을 사용한다. 실패하거나 key가 없으면 fallback summary/evidence를 생성한다.
- `app/schemas/ai_contract.py`: `EvidenceChunk`에는 `chunk_id`, `policy_id`, `snippet`, `source_title`, `source_url`, `score`, `evidence_role`이 있다.
- `app/schemas/policy_summary_schema.py`: 정책 상세 요약 API 응답은 `status`, `summary`, `evidence: list[str]`만 노출한다.
- `app/services/policy_summary_service.py`: 생성 결과를 sanitize한 뒤 summary와 문자열 evidence만 cache/API 응답으로 저장한다.
- `app/ai/nodes/chat/result_adapters.py`: 채팅 policy summary 응답은 `evidence_chunks`를 chat evidence로 변환할 수 있지만, 요약 문장별 citation 관계는 없다.

따라서 현재 시스템은 "검색된 chunk가 맞았는가"와 "생성된 각 문장이 실제 policy field 또는 chunk에 의해 지지되는가"를 자동으로 같은 증거로 주장할 수 없다.

## Options

| Option | Change Scope | Reliability | Cost | Consumer Impact | Portfolio Value |
| --- | --- | --- | --- | --- | --- |
| A. 샘플 수동 rubric | 평가 문서/샘플 파일만 추가. 런타임 계약 변경 없음. | 중간. 사람이 문장별 근거를 확인하므로 hallucination은 잘 잡지만 자동 회귀 검출력은 낮다. | 낮음. 10개 정책 샘플이면 빠르게 시작 가능. | 없음. API/schema/frontend 변경 없음. | 높음. "생성 답변 grounding은 별도 수동 샘플 평가로 확인"이라고 안전하게 말할 수 있다. |
| B. 구조화 citation | generator output, cache schema, API response, chat/detail consumers 변경 필요. | 높음. 문장 또는 claim이 `chunk_id`/policy field에 직접 연결된다. | 높음. migration, backward compatibility, UI 표시 정책까지 필요. | 큼. 기존 `evidence: list[str]` 소비자와 상세/채팅 응답 계약을 조정해야 한다. | 매우 높음. 포트폴리오에서 가장 강한 traceability 주장 가능. |
| C. 문자열 overlap/규칙 검사 | 평가 스크립트만 추가 가능. 선택적으로 CI smoke 가능. | 낮음~중간. 한국어 paraphrase에서 거짓 실패가 많고, 단어가 겹쳐도 의미가 다를 수 있다. | 낮음. 빠르게 자동화 가능. | 없음 또는 낮음. 런타임에 붙이지 않으면 소비자 영향 없음. | 중간. 보조 smoke로는 좋지만 grounding correctness 단독 근거로는 약하다. |
| D. LLM judge | 평가 스크립트와 judge prompt/rubric 필요. API 비용과 반복 실행 관리 필요. | 중간~높음. 의미 비교는 가능하지만 judge 변동성, 자기평가 편향, 비용 문제가 있다. | 중간~높음. 재현성 확보를 위해 고정 샘플, judge 버전, 재실행 정책이 필요하다. | 없음. 오프라인 평가로 유지하면 소비자 영향 없음. | 중간. 보조 설명 가치는 있지만 현재 "최소 계약"으로는 과하다. |

## Recommendation

U5 이후 바로 진행할 최소 단계는 A. 샘플 수동 rubric이다.

권장 이유:

- 현재 API 응답은 문장별 citation을 노출하지 않으므로 B를 바로 구현하면 schema/cache/frontend 영향이 커진다.
- retrieval benchmark는 이미 actual repeated benchmark로 정리되었지만, 그것은 검색 증거의 정답률이지 생성 답변의 faithfulness가 아니다.
- 수동 rubric은 구현 범위가 작고, 포트폴리오 문구를 안전하게 분리할 수 있다.
- C는 A의 보조 smoke로만 적합하다.
- D는 비용과 변동성을 고려하면 B 설계 승인 이후 또는 평가 자동화 단계에서 다시 검토하는 편이 낫다.

## Proposed Manual Rubric

샘플 10개 정책을 고르고, 각 생성 결과를 문장 단위 claim으로 쪼갠다. 각 claim은 아래 중 하나에 매핑한다.

| Field | Meaning |
| --- | --- |
| `claim_text` | 생성 summary/evidence에 포함된 사용자 표시 문장 또는 핵심 주장 |
| `support_type` | `policy_field`, `retrieved_chunk`, `both`, `unsupported`, `unclear` |
| `support_reference` | policy field name 또는 `chunk_id` |
| `grounding_status` | `grounded`, `partially_grounded`, `unsupported`, `not_checkable` |
| `issue_type` | `none`, `hallucinated_condition`, `wrong_amount`, `wrong_period`, `wrong_method`, `overgeneralized`, `generic_only` |
| `review_note` | 사람이 본 짧은 근거 |

집계 지표:

- Claim Grounding Rate = grounded claim / checkable claim
- Unsupported Claim Count
- Critical Error Count = 금액, 대상, 기간, 신청 방법 오류
- Generic Evidence Rate = 실제 정책별 근거 없이 일반 문구만 남은 비율

## Evidence Boundary

포트폴리오와 문서에서 사용할 수 있는 표현:

- "검색 근거 평가는 50개 내부 goldset에서 반복 실행으로 검증했다."
- "생성 답변 grounding은 검색 평가와 별도이며, 수동 rubric으로 문장별 근거 매핑을 설계했다."
- "현재 단계에서는 retrieved evidence correctness를 generated answer faithfulness로 등치하지 않는다."

아직 말하면 안 되는 표현:

- "생성 답변이 모두 원문 근거와 자동 연결된다."
- "요약 문장별 citation이 API로 제공된다."
- "LLM 답변 faithfulness를 end-to-end benchmark로 검증했다."

## Next Implementation Gate

다음 단계로 진행하려면 아래 중 하나를 먼저 승인해야 한다.

1. A only: `docs/eval` 또는 `tests/eval/fixtures`에 10개 샘플 rubric 파일과 수동 평가 리포트를 추가한다.
2. A + C: 수동 rubric을 기준으로 문자열 overlap smoke를 추가하되, 자동 pass/fail은 보조 신호로만 둔다.
3. B design: `PolicySummaryGeneration`에 structured citation을 추가하고 cache/API/chat/detail 소비자 영향 범위를 별도 설계한다.

현재 권장안은 1번이다.
