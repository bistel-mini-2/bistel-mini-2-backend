# Recommendation Swagger Test Cases

이 문서는 추천 요청 생성/조회 흐름을 Swagger에서 수동 검증하기 위한 케이스 모음이다.

## 공통 테스트 순서

1. 서버 실행 후 Swagger 접속
   - `http://localhost:8000/docs`
2. 인증이 필요한 환경이면 `Authorize` 설정
3. `POST /api/v1/recommendations/requests` 실행
4. 응답의 `data.request_id` 복사
5. `GET /api/v1/recommendations/requests/{request_id}` 반복 호출
6. `data.status`가 `loading`에서 `done` 또는 `error`로 바뀌는지 확인

정상 추천 완료 기준:

- `data.status = "done"`
- `data.error_message = null`
- `data.results`가 비어 있지 않음
- 결과 item에 `policy_id`, `policy_name`, `summary`, `reason_summary`, `user_status`, `assessment_status`가 있음
- LLM 성공 시 결과 item 일부 또는 전체에 `rerank_score`, `recommendation_reason`, `used_evidence_chunk_ids`가 있음
- LLM 일부 응답만 성공하면 backfill item에 `llm_backfilled = true`가 있을 수 있음
- LLM 실패 fallback이면 요청은 `error`가 아니라 `done`이어야 함

주의:

- `result_json.summary`는 현재 추천 폴링 GET 응답에 직접 노출되지 않는다. `llm_fallback_used`, `llm_error`, `llm_candidate_pool_count` 같은 summary 값은 DB에서 확인한다.
- `selected_conditions.needs`는 필수 입력이 아니다. 자연어 `raw_query`가 있으면 ConditionAgent가 관심사를 추출한다. 프론트에 별도 관심사 선택 UI가 있을 때만 `needs`를 보낸다.

## Case 1. 폼 필수값 + 자연어 정상 입력

프론트에서 필수 select 값을 모두 채우고, 사용자가 자연어 요청도 입력한 기본 케이스다.

```json
{
  "source_type": "FORM",
  "source_ref_id": "recommendation_form_full_with_raw_query",
  "raw_query": "서울에 살고 있고 0세 아이를 키우고 있어. 맞벌이 가정이고 양육비나 출산 지원금 위주로 추천해줘.",
  "selected_conditions": {
    "region": "seoul",
    "stage": "infant",
    "childAge": "0",
    "income": "low",
    "special": ["dual"]
  }
}
```

확인:

- `GET` 최종 응답 `data.status = "done"`
- `data.follow_up_questions = []`
- `data.results.length > 0`
- `results[0].evidence.length > 0`이면 RAG evidence 연결 정상
- `results[0].reason_summary`가 한글 문장
- LLM 성공 시 `rerank_score`가 포함됨

DB 확인:

- `merged_condition_json.region = "seoul"`
- `merged_condition_json.stage = "infant"`
- `merged_condition_json.childAge = "0"`
- `merged_condition_json.income = "low"`
- `result_json.summary.llm_rerank_used = true` 또는 fallback이면 `false`

## Case 2. 자연어만 입력

채팅/검색형 UX에서 폼 선택 없이 자연어만으로 조건 추출이 가능한지 확인한다.

```json
{
  "source_type": "CHAT",
  "source_ref_id": "chat_message:test-raw-only",
  "raw_query": "서울에 살고 있고 0세 아이를 키우는 저소득 맞벌이 가정이야. 양육비와 출산 지원금 관련 정책을 추천해줘."
}
```

확인:

- `data.status = "done"`이면 자연어 추출 후 추천까지 완료
- `data.status = "done"`이고 `follow_up_questions`가 있으면 핵심 조건 추출이 부족했던 것
- `data.status = "error"`이면 OpenAI 설정 또는 ConditionAgent 호출 실패 확인

DB 확인:

- `parsed_query_json.raw_query_extracted`에 `region`, `stage` 또는 `childAge`, `income`, `special`, `needs`가 들어갔는지 확인
- `merged_condition_json`에 추출된 조건이 반영됐는지 확인

## Case 3. 폼만 입력

자연어 없이 프론트 select 값만으로 추천이 가능한지 확인한다.

```json
{
  "source_type": "FORM",
  "source_ref_id": "recommendation_form_only",
  "selected_conditions": {
    "region": "seoul",
    "stage": "infant",
    "childAge": "0",
    "income": "low",
    "special": ["dual"],
    "needs": ["양육비", "출산 지원금"]
  }
}
```

확인:

- `data.status = "done"`
- `data.follow_up_questions = []`
- `data.results.length > 0`
- 자연어가 없으므로 `parsed_query_json.raw_query_extracted`는 비어 있어도 정상

비고:

- 이 케이스의 `needs`는 프론트에 관심사 선택 UI가 있을 때의 예시다.
- 일반 자유입력 UX에서는 `needs`를 보내지 않고 `raw_query`에서 추출하게 두는 쪽이 자연스럽다.

## Case 4. 핵심 필드 부족: region 없음

지역이 없으면 follow-up이 발생하는지 확인한다.

```json
{
  "source_type": "FORM",
  "source_ref_id": "recommendation_missing_region",
  "selected_conditions": {
    "stage": "infant",
    "childAge": "0",
    "income": "low",
    "special": ["dual"]
  }
}
```

확인:

- 최종 `GET` 응답은 `data.status = "done"`일 수 있다.
- `data.follow_up_questions.length > 0`
- 질문 중 `region` 관련 질문이 있어야 함
- 추천 결과는 없거나 비어 있을 수 있음

DB 확인:

- `request_status = "FOLLOW_UP_REQUIRED"`
- `parsed_query_json.input_issues`에 `field_name = "region"`, `issue_type = "missing"` 존재

## Case 5. 덜 중요한 필드 부족: income 없음

소득이 없어도 추천 흐름을 막지 않고, 내부 issue만 남기는지 확인한다.

```json
{
  "source_type": "FORM",
  "source_ref_id": "recommendation_missing_income",
  "raw_query": "서울에서 0세 아이를 키우고 있고 양육비 지원을 알고 싶어.",
  "selected_conditions": {
    "region": "seoul",
    "stage": "infant",
    "childAge": "0",
    "special": []
  }
}
```

확인:

- `data.status = "done"`
- 보통 `follow_up_questions = []`
- `results`가 생성되어야 함

DB 확인:

- `parsed_query_json.input_issues`에 `income missing`이 있을 수 있음
- `request_status = "COMPLETED"`이면 소득 부족이 추천 흐름을 막지 않은 것

## Case 6. 자연어와 폼 값이 다름: 폼 값 우선

자연어는 부산/1세를 말하지만 폼은 서울/0세로 선택된 경우다. 현재 입력 확정값인 `selected_conditions`가 우선되어야 한다.

```json
{
  "source_type": "FORM",
  "source_ref_id": "recommendation_raw_form_mismatch",
  "raw_query": "부산에 살고 있고 1세 아이를 키우고 있어. 보육료 지원을 추천해줘.",
  "selected_conditions": {
    "region": "seoul",
    "stage": "infant",
    "childAge": "0",
    "income": "low",
    "special": ["dual"]
  }
}
```

확인:

- `data.status = "done"`
- 추천 결과가 생성됨

DB 확인:

- `parsed_query_json.raw_query_extracted.region = "busan"`일 수 있음
- `parsed_query_json.selected_conditions.region = "seoul"`
- `merged_condition_json.region = "seoul"`
- `merged_condition_json.childAge = "0"`

## Case 7. 잘못된 enum 값

백엔드 방어 검증이 동작하는지 확인한다. `stage = "baby"`는 허용 값이 아니다.

```json
{
  "source_type": "FORM",
  "source_ref_id": "recommendation_invalid_stage",
  "selected_conditions": {
    "region": "seoul",
    "stage": "baby",
    "childAge": "0",
    "income": "low",
    "special": ["dual"]
  }
}
```

확인:

- `childAge`가 있으므로 추천은 `done`까지 갈 수 있음
- 결과가 생성되면 invalid stage가 전체 요청을 깨지 않는 것

DB 확인:

- `parsed_query_json.input_issues`에 `field_name = "stage"`, `issue_type = "invalid"` 존재
- `merged_condition_json.stage`는 없거나 프로필 값으로 대체될 수 있음
- `merged_condition_json.childAge = "0"`

## Case 8. LLM fallback 확인

LLM rerank 실패 시 요청 전체가 실패하지 않고 #96 기반 결과로 fallback되는지 확인한다.

테스트 방법:

1. 서버 환경에서 OpenAI 키를 의도적으로 제거하거나 잘못된 값으로 설정
2. 자연어를 넣으면 ConditionAgent도 실패할 수 있으므로 `raw_query` 없이 폼만 입력
3. 아래 요청 실행

```json
{
  "source_type": "FORM",
  "source_ref_id": "recommendation_llm_fallback",
  "selected_conditions": {
    "region": "seoul",
    "stage": "infant",
    "childAge": "0",
    "income": "low",
    "special": ["dual"],
    "needs": ["양육비"]
  }
}
```

확인:

- `data.status = "done"`이어야 함
- `data.results.length > 0`
- `rerank_score`가 없거나 일부만 있을 수 있음

DB 확인:

- `result_json.summary.llm_fallback_used = true`
- `result_json.summary.llm_error`에 LLM 실패 원인이 있음
- `request_status = "COMPLETED"`

## Case 9. 특수조건 다자녀/장애

special 조건이 후보 검색과 rule filter에 반영되는지 확인한다.

```json
{
  "source_type": "FORM",
  "source_ref_id": "recommendation_special_many_disabled",
  "raw_query": "서울에 살고 있고 2세 아이를 키우고 있어. 다자녀나 장애아동 관련 지원을 보고 싶어.",
  "selected_conditions": {
    "region": "seoul",
    "stage": "infant",
    "childAge": "2-5",
    "income": "mid1",
    "special": ["many", "disabled"]
  }
}
```

확인:

- `data.status = "done"`
- 결과 정책의 `reason_summary` 또는 `matched_conditions`에 다자녀/장애 관련성이 드러나는지 확인
- evidence가 관련 정책 문서 근거를 포함하는지 확인

## DB 확인 쿼리

`request_id`는 Swagger POST 응답에서 받은 값으로 바꿔 실행한다.

### recommendation_request 확인

```sql
SELECT
  request_id,
  request_status,
  error_message,
  result_json->'summary'->>'llm_rerank_used' AS llm_rerank_used,
  result_json->'summary'->>'llm_fallback_used' AS llm_fallback_used,
  result_json->'summary'->>'llm_candidate_pool_count' AS llm_candidate_pool_count,
  result_json->'summary'->>'llm_selected_count' AS llm_selected_count,
  result_json->'summary'->>'llm_backfilled_count' AS llm_backfilled_count,
  jsonb_pretty(parsed_query_json) AS parsed_query_json,
  jsonb_pretty(merged_condition_json) AS merged_condition_json,
  jsonb_pretty(result_json->'summary') AS result_summary
FROM recommendation_request
WHERE request_id = :request_id;
```

### 최종 results 확인

```sql
SELECT
  result_item->>'policy_id' AS policy_id,
  result_item->>'policy_name' AS policy_name,
  result_item->>'user_status' AS user_status,
  result_item->>'assessment_status' AS assessment_status,
  result_item->>'rerank_score' AS rerank_score,
  result_item->>'llm_backfilled' AS llm_backfilled,
  jsonb_array_length(COALESCE(result_item->'evidence', '[]'::jsonb)) AS evidence_count,
  result_item->>'reason_summary' AS reason_summary
FROM recommendation_request r,
LATERAL jsonb_array_elements(r.result_json->'results') AS result_item
WHERE r.request_id = :request_id;
```

### recommendation_candidate 확인

```sql
SELECT
  policy_id,
  candidate_status,
  retrieval_score,
  rerank_score,
  filter_match_json->'candidate_search' AS candidate_search
FROM recommendation_candidate
WHERE request_id = :request_id
ORDER BY rerank_score DESC NULLS LAST, retrieval_score DESC
LIMIT 20;
```

### policy_assessment 확인

```sql
SELECT
  policy_id,
  assessment_status,
  confidence_score,
  selected_for_result,
  reason_summary
FROM policy_assessment
WHERE recommendation_request_id = :request_id
  AND assessment_type = 'recommendation_assessment'
ORDER BY selected_for_result DESC, confidence_score DESC;
```

## 기대 저장 흐름

- `recommendation_request.parsed_query_json`
  - raw_query 추출 결과
  - selected_conditions
  - input_issues
  - follow-up questions
- `recommendation_request.merged_condition_json`
  - 최종 추천 조건
  - raw와 selected가 다르면 selected 값이 우선 반영됨
- `recommendation_candidate`
  - 후보 정책
  - retrieval_score
  - candidate_status
  - LLM 성공 시 선택 정책의 rerank_score
- `policy_assessment`
  - 후보별 내부 5상태 assessment_status
  - confidence_score
  - selected_for_result
- `recommendation_request.result_json`
  - 최종 results/recommendations
  - evidence
  - LLM reason/rerank 결과
  - fallback summary
