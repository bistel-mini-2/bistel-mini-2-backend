# Agent Workflow Design

> Source: `최최종` PDF, 2026-06-18

## 0. 바이브코딩용 사용법

이 문서는 사람이 읽는 설계서이면서, AI에게 구현을 맡길 때 기준 문서로 쓰는 것을 전제로 한다.

바이브코딩할 때는 아래 원칙을 먼저 AI에게 전달한다.

```text
이 문서를 기준으로 구현한다.
임의로 Agent / Graph 구조를 바꾸지 않는다.
Section 2의 추천/판단 공통 원칙은 Recommendation Graph와 Eligibility Graph에 반드시 적용한다.
각 기능은 Section 4의 Node 흐름을 기준으로 구현한다.
DB 저장, RAG 검색, 정책 조회는 Tool로 분리한다.
내부 상태는 사용자에게 그대로 노출하지 않는다.
```

AI에게 한 번에 전체 시스템을 만들게 하기보다, 아래 단위로 나눠 요청하는 것이 좋다.

| 구현 단위 | 참고 섹션 | 완료 기준 |
| --- | --- | --- |
| Condition Agent | 2.1-2.3, 3.1 | 자연어/필드 입력을 동일한 조건 JSON으로 정규화한다. |
| Rule Filter Node | 2.4, 3.2 | 명확한 탈락 정책만 제외하고 애매한 정책은 후보로 유지한다. |
| Policy Assessment Agent | 2.5-2.6, 3.3 | 5개 내부 상태를 만들고 사용자 노출 상태로 매핑한다. |
| Recommendation Graph | 4.1 | 조건 정리, 후보 검색, 룰 필터, 평가, RAG, 추천 저장까지 연결한다. |
| Eligibility Graph | 4.2 | 특정 정책 1건에 대해 같은 상태 모델로 정밀 판단한다. |
| Comparison Graph | 4.3 | 두 정책을 사용자 조건 기준으로 비교하고 상황별 선택 가이드를 만든다. |
| Application Preparation Graph | 4.4 | 신청 정보, 필요서류, 체크리스트를 사용자 조건에 맞게 생성한다. |
| Chat Supervisor Graph | 4.6 | intent를 분류하고 기존 기능별 Graph를 재사용한다. |

구현 프롬프트 예시는 다음처럼 쓴다.

```text
docs/agent-workflow-design.md를 기준으로 Recommendation Graph를 구현해줘.
범위는 Section 4.1만이다.
Section 2의 입력 처리, Rule Filter, Policy Assessment 상태 규칙을 반드시 반영해줘.
Tool은 실제 DB 연결 대신 인터페이스와 mock 구현부터 만들어줘.
각 Node는 입력/출력 state 타입이 드러나게 작성해줘.
```

바이브코딩 시 특히 흔한 실수는 다음과 같다.

- 챗봇 안에 추천/비교/신청 로직을 새로 만드는 것
- Recommendation과 Eligibility의 상태 체계를 따로 만드는 것
- `missing`, `ambiguous`, `invalid`를 하나의 "정보 부족"으로 뭉개는 것
- `NEEDS_MORE_INFO`와 `INSUFFICIENT_PROFILE`을 구분하지 않는 것
- 내부 상태를 사용자에게 그대로 보여주는 것
- Rule Filter Node에서 애매한 정책까지 제거하는 것
- RAG 검색과 DB 조회를 Agent 내부 프롬프트에 섞어버리는 것

## 1. 설계 방향

이 프로젝트의 AI 기능은 챗봇 내부 기능이 아니라, 각 화면에서 직접 실행되는 LangGraph 기반 워크플로우로 설계한다.

챗봇은 별도의 추천, 비교, 신청 로직을 새로 구현하지 않고 이미 구현된 기능별 Graph를 Supervisor Agent가 라우팅해서 재사용하는 통합 인터페이스 역할을 한다.

```text
정책 추천 화면 -> Recommendation Graph
지원 가능성 화면 -> Eligibility Graph
정책 비교 화면 -> Comparison Graph
신청 준비 화면 -> Application Preparation Graph
정책 상세 화면 -> Policy Summary Graph
챗봇 화면 -> Chat Supervisor Graph -> 기능별 Graph 재사용
```

핵심 평가 포인트는 다음과 같다.

- 구조화 출력: 사용자 입력을 조건 JSON, 비교 기준 JSON, 체크리스트 JSON 등으로 변환한다.
- Tool Calling: 정책 조회, 후보 검색, rule 조회, RAG 검색, 이력 저장을 도구로 호출한다.
- RAG: 정책 원문 chunk를 검색하고 근거 chunk를 결과와 함께 반환한다.
- Multi-Agent: 여러 Agent가 순차적으로 상태를 보강하면서 최종 결과를 만든다.
- Graph: 기능별 실행 흐름을 Node 단위로 분리하고 상태를 저장한다.

## 2. 추천/판단 공통 원칙

### 2.1 입력 진입점

입력 진입점은 2가지다.

- 자연어 입력
- 필드 직접 입력

두 입력은 앞단 처리만 다르고, 중간부터는 같은 파이프라인으로 합쳐진다.

```text
자연어 입력
-> 입력 정리(condition extraction)

필드 직접 입력
-> 입력 검증/정규화(validation / normalization)

이후 공통
-> 프로필 병합
-> Rule Filter Node
-> Policy Assessment
-> 필요 시 후속질문
-> 사용자 결과 노출
```

### 2.2 입력 정리 단계 상태

입력 정리 단계에서는 아래 3가지를 구분한다.

- `missing`: 정보가 비어 있거나 아직 받지 못한 상태
- `ambiguous`: 여러 해석이 가능해서 추가 확인이 필요한 상태
- `invalid`: 오타, 형식 오류, 정규화 실패 등으로 바로 사용할 수 없는 상태

이 3가지는 모두 같은 의미가 아니다.

- `missing`은 후속질문 후보가 될 수 있다.
- `ambiguous`는 추천 결과를 바꿀 가능성이 크면 후속질문으로 연결한다.
- `invalid`는 먼저 입력 보정 또는 재입력이 필요하다.

여기서 `핵심 필드`를 별도로 둔다. 핵심 필드는 Recommendation / Eligibility의 결과를 실제로 크게 바꿀 수 있는 최소 조건 세트다.

예시는 다음과 같다.

- 거주 지역
- 생애 단계(임신, 출산, 양육 등)
- 자녀 수 또는 자녀 연령대
- 가구 유형
- 소득 구간

핵심 필드 정의는 정책군에 따라 달라질 수 있지만, 최소한 "이 값이 바뀌면 rule filter 결과가 달라질 수 있는가"를 기준으로 선정한다.

### 2.3 프로필 병합 원칙

저장 프로필과 현재 입력이 충돌하면 다음 원칙을 따른다.

- 현재 입력 우선
- 내부적으로 충돌 기록 저장
- 추천 결과를 바꿀 정도의 충돌일 때만 사용자에게 "현재 입력 기준으로 안내드릴게요"를 노출

즉, 프로필은 기본값 역할을 하지만 현재 세션 입력을 덮어쓰지 않는다.

저장 기준은 다음처럼 둔다.

- `parsed_query_json`: 프로필 병합 전 입력 해석 결과
- `merged_condition_json`: 병합 후 최종 채택된 조건 세트
- `profile_conflict_json`: 저장 프로필과 현재 입력의 충돌 기록

### 2.4 Rule Filter Node와 Policy Assessment 분리

후보를 좁히는 단계와 최종 판단 단계는 분리한다.

Rule Filter Node:

- LLM 없이 규칙 기반으로 처리한다.
- 명확히 아닌 정책은 제외한다.
- 애매한 조건은 후보를 유지한 채 Assessment로 넘긴다.

Policy Assessment:

- 후보 정책별 최종 판정 단계다.
- Rule Filter Node를 통과한 후보에 대해 지원 가능성, 추가 확인 필요 여부, 충돌 여부, 설명 근거를 만든다.

### 2.5 Assessment 내부 상태

`policy_assessment`의 내부 상태는 다음 5개로 통일한다.

| 상태 | 의미 |
| --- | --- |
| `LIKELY_MATCH` | 현재 정보 기준으로 추천 가능성이 높다. |
| `NEEDS_MORE_INFO` | 일부 추가 정보에 따라 결과 정밀도가 달라질 수 있지만, 현재 정규화 결과 기준으로는 추천 방향이 크게 흔들리지 않는다. |
| `NOT_MATCH` | 현재 정보 기준으로 핵심 조건이 맞지 않는다. |
| `INSUFFICIENT_PROFILE` | 판단에 필요한 핵심 필드가 많이 비어 있어 rule filter 또는 assessment의 신뢰도가 낮다. |
| `CONFLICTING_PROFILE` | 입력 간 충돌로 인해 정규화 결과를 하나로 안정적으로 고정하기 어렵고, 어떤 값을 기준으로 잡느냐에 따라 rule filter 결과가 달라진다. |

경계 기준은 다음처럼 둔다.

`NEEDS_MORE_INFO`:

- 핵심 필드가 1개 부족하거나
- 모호성이 있어도 현재 정규화 결과로 rule filter를 돌렸을 때 후보군 변화가 크지 않은 경우

`INSUFFICIENT_PROFILE`:

- 핵심 필드가 2개 이상 비어 있거나
- 정보 부족 때문에 rule filter 또는 assessment를 돌려도 결과 신뢰도가 너무 낮은 경우

`CONFLICTING_PROFILE`:

- 현재 입력과 저장 프로필, 또는 입력 내부 값끼리 충돌이 있고
- 그 충돌 때문에 정규화 결과를 2가지 이상으로 만들 수 있으며
- 각 정규화 결과로 rule filter를 돌렸을 때 후보 정책이나 결과 상태가 실제로 달라지는 경우

반대로, 현재 입력 우선 원칙으로 충돌을 해소했을 때 정규화 결과가 안정적이고 rule filter 결과도 사실상 같다면 `CONFLICTING_PROFILE`로 올리지 않는다. 이 경우는 `NEEDS_MORE_INFO` 또는 일반 진행 대상으로 처리한다.

### 2.6 사용자 노출 상태 단순화

내부 상태를 사용자에게 그대로 보여주지 않는다. 사용자 노출 상태는 3단계로 단순화한다.

| 내부 상태 | 사용자 노출 상태 |
| --- | --- |
| `LIKELY_MATCH` | 추천 가능 |
| `NEEDS_MORE_INFO` | 추가 확인 필요 |
| `INSUFFICIENT_PROFILE` | 추가 확인 필요 |
| `CONFLICTING_PROFILE` | 추가 확인 필요 |
| `NOT_MATCH` | 추천 어려움 |

### 2.7 후속질문 규칙

후속질문은 다음 원칙으로 제한한다.

- 최대 2개
- 추천 결과를 실제로 바꿀 가능성이 큰 정보만 질문
- 이미 결과가 거의 고정된 경우에는 질문하지 않고 현재 기준으로 안내

즉, `missing`이나 `ambiguous`가 있다고 해서 항상 질문하지 않는다.

후속질문은 다음 경우에만 생성한다.

- 빠진 핵심 필드가 1개 수준이라 보완 가치가 큰 경우
- 모호한 값 때문에 정규화 결과 후보가 갈리며, 그 차이가 rule filter 또는 assessment 결과를 바꿀 수 있는 경우

반대로 다음 경우에는 질문을 줄이거나 생략한다.

- 핵심 필드가 2개 이상 비어 있어 우선 `INSUFFICIENT_PROFILE`로 처리하는 것이 더 자연스러운 경우
- 현재 입력 우선으로 해석해도 결과가 거의 동일한 경우
- 이미 `NOT_MATCH`가 명확한 경우

## 3. 공통 Agent

### 3.1 Condition Agent

사용자 조건을 모든 AI 기능에서 재사용 가능한 표준 조건으로 정리하는 Agent다.

사용 위치:

- Recommendation Graph
- Eligibility Graph
- Comparison Graph
- Application Preparation Graph
- Chat Supervisor Graph의 intent 결과 보강

주요 역할:

- 자연어 입력 정리(condition extraction)
- 필드 입력 검증/정규화(validation / normalization)
- 사용자 프로필과 현재 요청 조건 병합
- 입력 상태 판별(`missing`, `ambiguous`, `invalid`)
- 정책 검색과 판단에 사용할 condition tag 추출
- 추천 결과를 바꿀 가능성이 큰 경우에만 후속질문 후보 생성
- 프로필 충돌 기록 생성

입력 예시:

```json
{
  "selected_conditions": {
    "region": "서울",
    "life_stage": "출산 예정",
    "children_count": 0,
    "income_range": "중위소득 100% 이하"
  },
  "natural_language": "맞벌이고 곧 첫째 출산 예정이에요. 신청 서류가 복잡한 건 피하고 싶어요.",
  "profile_id": 12
}
```

출력 예시:

```json
{
  "normalized_condition": {
    "region": "서울",
    "life_stage": "pregnancy",
    "household_type": ["dual_income"],
    "children_count": 0,
    "income_range": "median_100_or_below",
    "preference": ["simple_application"]
  },
  "condition_tags": ["pregnancy", "first_child", "dual_income", "seoul", "low_middle_income"],
  "input_issues": {
    "missing": [],
    "ambiguous": [],
    "invalid": []
  },
  "profile_conflicts": [
    {
      "field": "region_code",
      "profile_value": "SEOUL",
      "input_value": "GYEONGGI",
      "resolved_value": "GYEONGGI",
      "resolution_rule": "prefer_current_input",
      "changes_rule_filter_result": true
    }
  ],
  "follow_up_candidates": [],
  "follow_up_question": null
}
```

### 3.2 Rule Filter Node

추천용 후보를 좁히는 규칙 기반 단계다. LLM 없이 룰 엔진으로 처리한다.

주요 역할:

- 지역, 연령, 생애주기, 소득구간, 가구유형 등 명시적 조건 필터링
- 명확한 탈락 정책 제외
- 애매한 조건은 제외하지 않고 후보 유지

출력 예시:

```json
{
  "filtered_candidates": [
    {
      "policy_id": 1,
      "rule_result": "pass"
    },
    {
      "policy_id": 2,
      "rule_result": "uncertain"
    }
  ],
  "excluded_candidates": [
    {
      "policy_id": 3,
      "rule_result": "fail",
      "reason": "region_mismatch"
    }
  ]
}
```

### 3.3 Policy Assessment Agent

후보 정책과 사용자 조건을 바탕으로 정책별 최종 판정 상태를 만든다.

주요 역할:

- 후보 정책별 상태 판정
- 추가 확인이 필요한 이유 정리
- 충돌 또는 정보 부족 여부 판별
- 최종 추천 설명 생성의 입력 제공

판단 표현:

내부 상태는 항상 다음 5개를 사용한다.

- `LIKELY_MATCH`
- `NEEDS_MORE_INFO`
- `NOT_MATCH`
- `INSUFFICIENT_PROFILE`
- `CONFLICTING_PROFILE`

### 3.4 Recommendation Agent

Rule Filter Node 결과와 Policy Assessment 결과, RAG 근거를 바탕으로 TOP N 추천 결과를 만든다.

### 3.5 Comparison Agent

정책 2개를 비교하고, 사용자 조건을 반영해 공통점, 차이점, 상황별 선택 가이드를 만든다.

### 3.6 Application Preparation Agent

정책 신청을 준비할 수 있도록 신청방법, 필요서류, 주의사항, 사용자 맞춤 체크리스트를 만든다.

### 3.7 Policy Q&A Agent

정책 상세 요약과 일반 정책 질문 답변을 담당한다.

### 3.8 Supervisor Agent

챗봇에서만 사용한다. 사용자 질문의 intent를 분류하고 필요한 기능 Graph를 호출한다.

## 4. 기능별 Graph

### 4.1 Recommendation Graph

목적:

사용자 조건을 분석하고 정책 후보를 검색한 뒤, Rule Filter Node와 Policy Assessment, RAG 근거를 바탕으로 추천 결과를 생성한다.

Agent 흐름:

```text
Condition Agent
-> Rule Filter Node
-> Policy Assessment Agent
-> Recommendation Agent
```

Node 흐름:

```text
START
-> Create Request Node
-> Input Route Node
   -> 자연어 입력: Condition Extraction Node
   -> 필드 입력: Validation / Normalization Node
-> Profile Merge Node
-> Condition Route Node
   -> invalid 있음: Input Correction Node -> END
   -> 핵심 필드 2개 이상 부족: Insufficient Profile 처리 -> END
   -> missing / ambiguous가 결과를 바꿀 가능성 큼: Follow-up Question Node -> END
   -> 현재 기준으로 진행 가능: Candidate Search Node
-> Rule Filter Node
-> Candidate Save Node
-> Policy Assessment Node
-> Evidence Search Node
-> Evidence Save Node
-> Recommendation Node
-> Recommendation Result Save Node
-> User Status Mapping Node
-> END
```

주요 Tool:

- `get_user_profile`
- `get_family_members`
- `normalize_user_condition`
- `search_policies_by_condition`
- `get_policy_rules`
- `retrieve_policy_chunks`
- `save_recommendation_candidates`
- `save_policy_assessment`
- `save_assessment_evidence`
- `save_profile_conflict_log`

기능명세 적합성:

- 추천 조건 입력
- 자연어 입력 / 필드 입력 공통 처리
- AI 조건 분석
- 정책 후보 검색
- Rule Filter Node
- Policy Assessment
- RAG 근거 검색
- TOP N 추천 카드
- 추천 이유 생성
- 추천 이력 저장

### 4.2 Eligibility Graph

목적:

특정 정책 1개에 대해 사용자의 지원 가능성을 정밀 분석한다. 이 Graph도 입력 처리 원칙은 Recommendation Graph와 같다. 다만 후보 다수 추천이 아니라 특정 정책 1건에 대한 판단이라는 점이 다르다.

Agent 흐름:

```text
Condition Agent
-> Policy Assessment Agent
```

Node 흐름:

```text
START
-> Policy Resolve Node
-> Input Route Node
   -> 자연어 입력: Condition Extraction Node
   -> 필드 입력: Validation / Normalization Node
-> Profile Merge Node
-> Rule Check Node
-> Condition Route Node
   -> invalid 있음: Input Correction Node -> END
   -> 핵심 필드 2개 이상 부족: Insufficient Profile 처리 -> END
   -> missing / ambiguous가 결과를 바꿀 가능성 큼: Follow-up Question Node -> END
   -> 현재 기준으로 진행 가능: Evidence Search Node
-> Policy Assessment Node
-> Assessment Save Node
-> Evidence Save Node
-> User Status Mapping Node
-> END
```

주요 Tool:

- `search_policy_by_name`
- `get_policy_detail`
- `get_policy_rules`
- `get_user_profile`
- `normalize_user_condition`
- `retrieve_policy_chunks`
- `save_policy_assessment`
- `save_assessment_evidence`
- `save_profile_conflict_log`

기능명세 적합성:

- 분석 대상 정책 선택
- 사용자 조건 입력
- 입력 정리 및 정규화
- RAG 기반 선정기준/지원대상 검색
- 내부 상태 기반 최종 판단
- 분석 근거 보기

Eligibility Graph도 Recommendation Graph와 동일하게 `policy_assessment`의 5개 내부 상태를 사용한다.

- `LIKELY_MATCH`
- `NEEDS_MORE_INFO`
- `NOT_MATCH`
- `INSUFFICIENT_PROFILE`
- `CONFLICTING_PROFILE`

즉, 특정 정책 1건을 깊게 보는 경우에도 별도 상태 체계를 두지 않고 같은 상태 모델 안에서 판단한다. 사용자에게는 이 내부 상태를 그대로 노출하지 않고 추천 가능 / 추가 확인 필요 / 추천 어려움으로 단순화한다.

DB 저장 기준:

정밀 분석도 `policy_assessment` 공통 테이블을 사용한다. 구분이 필요하면 `policy_assessment`에 다음 컬럼 추가를 권장한다.

```sql
assessment_type varchar(50)
```

값 예시:

```text
recommendation_assessment
eligibility_detail
```

`recommendation_request.source_type`도 함께 사용하면 어떤 화면 또는 챗봇 요청에서 실행된 분석인지 추적할 수 있다.

### 4.3 Comparison Graph

목적:

정책 2개를 단순 비교하는 데서 끝내지 않고, 사용자 조건을 기준으로 어떤 정책이 더 적합한지 상황별 선택 가이드를 제공한다.

기존 문서의 Comparison Graph는 `Comparison Agent + RAG Workflow`에 가까웠다. 기능명세의 "상황별 선택 가이드"를 더 잘 만족시키려면 Condition Agent를 앞단에 추가하는 것이 자연스럽다.

Agent 흐름:

```text
Condition Agent
-> Comparison Agent
```

Node 흐름:

```text
START
-> Validate Compare Target Node
-> Input Route Node
   -> 자연어 입력: Condition Extraction Node
   -> 필드 입력: Validation / Normalization Node
-> Profile Merge Node
-> Condition Route Node
   -> invalid 있음: Input Correction Node -> END
   -> missing / ambiguous 있음: Follow-up Question Node -> END
   -> 충분함: Policy Detail Load Node
-> Evidence Search Node
-> Comparison Node
-> Compare History Save Node
-> END
```

Node 역할:

| Node | 역할 | Agent |
| --- | --- | --- |
| Validate Compare Target Node | 비교 대상 정책 2개 검증 | 없음 |
| Input Route Node | 자연어 입력 / 필드 입력 경로 분기 | 없음 |
| Condition Route Node | `missing` / `ambiguous` / `invalid` 상태 분기 | 없음 |
| Policy Detail Load Node | 두 정책의 상세 정보 조회 | 없음 |
| Evidence Search Node | 지원대상, 지원내용, 선정기준, 신청방법 관련 RAG 근거 검색 | 없음 |
| Comparison Node | 비교표, 공통점/차이점, 사용자 상황별 선택 가이드 생성 | Comparison Agent |
| Compare History Save Node | 비교 실행 이력과 비교 대상 정책 저장 | 없음 |

주요 Tool:

- `get_policy_detail`
- `get_policy_rules`
- `get_user_profile`
- `normalize_user_condition`
- `retrieve_policy_chunks`
- `compare_policy_details`
- `get_similar_policies`
- `save_compare_history`
- `save_compare_history_items`

기능명세 적합성:

- 비교 대상 선택
- 비교 정보 수집
- 비교표 생성
- 공통점/차이점 정리
- 상황별 선택 가이드
- RAG 근거 기반 비교
- 비교 이력 저장

판단:

Condition Agent를 추가하는 것이 기능명세에 더 잘 맞다. 비교 기능의 핵심은 단순 정책 비교가 아니라 "사용자 상황에서 무엇을 먼저 볼지"를 알려주는 것이기 때문이다.

### 4.4 Application Preparation Graph

목적:

특정 정책 신청을 준비할 수 있도록 신청방법, 신청기간, 필요서류, 주의사항을 안내하고 사용자 상황에 맞는 체크리스트를 생성한다.

기존 문서의 Application Preparation Graph는 신청 정보와 RAG 근거를 모아 체크리스트를 만드는 구조였다. 기능명세의 "사용자 상황별 추가 서류 표시"를 만족시키려면 Condition Agent가 필요하다.

Agent 흐름:

```text
Condition Agent
-> Application Preparation Agent
```

Node 흐름:

```text
START
-> Policy Resolve Node
-> Input Route Node
   -> 자연어 입력: Condition Extraction Node
   -> 필드 입력: Validation / Normalization Node
-> Profile Merge Node
-> Condition Route Node
   -> invalid 있음: Input Correction Node -> END
   -> missing / ambiguous 있음: Follow-up Question Node -> END
   -> 충분함: Progress Upsert Node
-> Application Info Node
-> Required Document Node
-> Evidence Search Node
-> Checklist Node
-> Checklist Save Node
-> END
```

Node 역할:

| Node | 역할 | Agent |
| --- | --- | --- |
| Policy Resolve Node | 신청 준비 대상 정책 확인 | 없음 |
| Input Route Node | 자연어 입력 / 필드 입력 경로 분기 | 없음 |
| Condition Route Node | `missing` / `ambiguous` / `invalid` 상태 분기 | 없음 |
| Progress Upsert Node | 사용자별 정책 진행 상태 생성 또는 조회 | 없음 |
| Application Info Node | 신청방법, 기간, 문의처 조회 | 없음 |
| Required Document Node | 기본 필요서류 조회 | 없음 |
| Evidence Search Node | 신청방법, 필요서류, 주의사항 관련 RAG 근거 검색 | 없음 |
| Checklist Node | 사용자 맞춤 체크리스트 생성 | Application Preparation Agent |
| Checklist Save Node | 체크리스트 항목을 사용자별 진행 상태에 저장 | 없음 |

주요 Tool:

- `search_policy_by_name`
- `get_policy_detail`
- `get_required_documents`
- `get_user_profile`
- `get_family_members`
- `normalize_user_condition`
- `retrieve_policy_chunks`
- `generate_application_checklist`
- `create_or_get_user_policy_progress`
- `get_policy_checklist_template`
- `save_user_policy_checklist_items`
- `update_user_policy_progress`

Condition Agent가 추출해야 하는 신청 준비 조건:

- 거주 지역
- 자녀 수와 자녀 나이
- 임신/출산/양육 단계
- 가구 유형
- 맞벌이 여부
- 소득 구간
- 한부모, 다자녀, 장애, 외국인 등 추가 확인 가능성이 있는 조건
- 사용자가 피하고 싶은 신청 방식 또는 선호 방식

기능명세 적합성:

- 신청 정책 선택
- 신청 정보 조회
- 필요 서류 확인
- 신청 체크리스트 생성
- 신청 전 주의사항
- 사용자 상황별 추가 서류 표시
- RAG 근거 기반 안내
- 마이페이지 체크리스트 진행 상태 저장

판단:

Condition Agent를 추가하는 것이 기능명세에 더 잘 맞다. 신청 준비 기능은 정책의 일반 신청방법만 보여주는 것이 아니라, 사용자 조건에 따라 준비해야 할 항목이 달라질 수 있기 때문이다.

DB 저장 기준:

현재 DB 초안의 신청 준비 저장 구조는 다음처럼 해석한다.

```text
policy_checklist_template
= 정책별 기본 체크리스트 템플릿

user_policy_progress
= 사용자별 정책 신청 진행 상태

user_policy_checklist_item
= 사용자별 체크리스트 항목 상태
```

Application Preparation Agent가 만든 체크리스트는 기본적으로 `policy_checklist_template`을 바탕으로 사용자별 `user_policy_checklist_item`에 복사해 저장한다.

현재 체크리스트 항목 상태는 단순하게 아래 2개만 사용한다.

- `PENDING`
- `DONE`

사용자 조건 때문에 추가 확인이 필요한 항목은 우선 `user_policy_checklist_item.note`에 남기는 방향을 기본으로 한다. 필요 이상으로 상태 모델을 늘리지는 않는다.

### 4.5 Policy Summary Graph

목적:

정책 상세 화면에서 긴 정책 내용을 쉬운 설명으로 요약하고 근거를 제공한다.

Agent 흐름:

```text
Policy Q&A Agent
```

Node 흐름:

```text
START
-> Policy Detail Load Node
-> Summary Evidence Search Node
-> Policy Summary Node
-> END
```

주요 Tool:

- `get_policy_detail`
- `retrieve_policy_chunks`
- `generate_policy_summary`

기능명세 적합성:

- 정책 상세
- 정책 설명 생성
- 요약 및 근거 보기
- RAG 기반 답변

판단:

이 기능은 굳이 멀티에이전트로 만들 필요가 없다. `Agent + RAG Workflow`로 표현하는 것이 자연스럽다.

### 4.6 Chat Supervisor Graph

목적:

사용자 질문을 분석하고 필요한 기능 Graph로 라우팅한다.

Agent 흐름:

```text
Supervisor Agent
-> 기능별 Graph 호출
-> 기능별 Agent 실행
```

Node 흐름:

```text
START
-> Chat Session Load Node
-> User Message Save Node
-> Supervisor Node
-> Graph Router Node
   -> recommendation: Recommendation Graph
   -> eligibility: Eligibility Graph
   -> comparison: Comparison Graph
   -> application_preparation: Application Preparation Graph
   -> policy_summary: Policy Summary Graph
   -> unclear: Clarification Response Node
-> Assistant Message Save Node
-> Chat Evidence Save Node
-> Chat Policy Link Node
-> Chat Session Update Node
-> END
```

기능명세 적합성:

- 챗봇 자유질문
- 의도 분류 및 라우팅
- 기능 흐름 연동
- 대화 맥락 유지
- 근거 표시
- 상담 이력 저장

## 5. 기능별 기술 적용 정리

| 기능 | 구조화 출력 | RAG | Tool Calling | Graph | Multi-Agent |
| --- | --- | --- | --- | --- | --- |
| 맞춤 추천 | O | O | O | O | O |
| 지원 가능성 분석 | O | O | O | O | O |
| 정책 비교 | O | O | O | O | O |
| 신청 준비 | O | O | O | O | O |
| 정책 상세 AI 요약 | O | O | O | O | △ |
| 챗봇 | O | O | O | O | O |
| RAG 문서 적재 | X | O | O | 배치 | X |
| 회원/로그인 | X | X | X | X | X |
| 관심 정책 | X | X | X | X | X |

## 6. 발표용 설명

이 프로젝트는 챗봇만 멀티에이전트를 사용하는 구조가 아니다.

추천, 지원 가능성 분석, 정책 비교, 신청 준비 기능은 각각 독립적인 LangGraph 기반 AI 워크플로우로 구현된다.

특히 추천과 지원 가능성 분석은 다음 원칙을 공유한다.

- 입력 진입점은 자연어 입력과 필드 직접 입력의 2가지다.
- 두 입력은 앞단 처리만 다르고 이후에는 같은 파이프라인으로 합쳐진다.
- 프로필 병합 후 Rule Filter Node가 후보를 먼저 좁힌다.
- Policy Assessment가 정책별 최종 판정 상태와 설명 근거를 만든다.
- 내부 상태는 그대로 노출하지 않고 3단계 사용자 상태로 단순화한다.
- 후속질문은 최대 2개까지만, 결과를 실제로 바꿀 수 있는 정보에 한해 요청한다.

챗봇은 이 기능들을 다시 구현하지 않고 Supervisor Agent가 사용자의 intent를 분류해 기존 Graph를 호출한다. 따라서 챗봇은 멀티에이전트 기능의 유일한 사용처가 아니라, 서비스 전체의 AI Graph를 자연어로 실행하는 통합 입구다.

## 7. 최종 판단

Comparison Graph와 Application Preparation Graph에 Condition Agent를 추가하는 것은 기능명세에 맞다.

비교 기능은 단순히 두 정책의 정보를 나열하는 것이 아니라 사용자 상황에서 어떤 차이가 중요한지 설명해야 하므로 Condition Agent가 필요하다.

신청 준비 기능은 필요서류와 체크리스트가 사용자 조건에 따라 달라질 수 있으므로 Condition Agent가 필요하다.

또한 추천과 지원 가능성 분석의 기준 문서는 아래처럼 정리하는 것이 맞다.

```text
Recommendation Graph
= Condition Agent
+ Rule Filter Node
+ Policy Assessment Agent
+ Recommendation Agent

Eligibility Graph
= Condition Agent
+ Policy Assessment Agent

Comparison Graph
= Condition Agent + Comparison Agent

Application Preparation Graph
= Condition Agent + Application Preparation Agent

Policy Summary Graph
= Policy Q&A Agent + RAG

Chat Supervisor Graph
= Supervisor Agent + 기능별 Graph 재사용
```

핵심 철학은 다음과 같다.

- 입력 채널은 달라도 추천 엔진 본체는 하나다.
- Rule Filter Node로 후보를 좁힌다.
- Assessment에서 최종 판단과 설명을 만든다.
- Recommendation과 Eligibility 모두 동일한 5개 내부 상태를 사용한다.
- 사용자에게는 복잡한 내부 상태 대신 이해 가능한 3단계 결과만 노출한다.
