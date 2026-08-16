# 이력서용 정책 챗봇 검색 품질 개선 정리

## 가장 추천하는 이력서 문장

### 2개 bullet 버전

- 정책 챗봇의 검색 로직을 `PolicyRetriever` 계층으로 분리하고 PostgreSQL
  `ILIKE`, pgvector 의미 검색, weighted RRF 하이브리드를 동일 인터페이스로
  구현
- 정책명 노출을 제거한 50문항 골드셋과 Policy/Section Hit@5·MRR·latency
  평가기를 구축해 Hybrid의 Policy Hit@5 88%·MRR 0.7757을 검증하고,
  Vector 우선 Adaptive fallback으로 SQL 추가 조회를 broad 검색 14%,
  정책별 근거 검색 2%로 제한하며 실제 5회 반복 측정으로 latency 중앙값 확인

### 한 줄 버전

> 정책 챗봇에 교체 가능한 리트리버 계층과 50문항 정량 평가 체계를 구축하고, SQL·pgvector·weighted RRF를 비교해 Hybrid의 Policy Hit@5 88%·MRR 0.7757을 검증한 뒤 Vector 우선 Adaptive fallback으로 불필요한 SQL 실행을 제한

### AI·검색 직무 강조 버전

> 정책명과 원문 표현을 직접 노출하지 않은 50문항 다중 정답 골드셋을 설계하고 Policy Hit@5, Section Hit@5, MRR 평가를 자동화해 검색 결과뿐 아니라 답변에 필요한 근거 섹션과 순위 품질까지 정량 검증

### 백엔드 설계 강조 버전

> 에이전트의 툴 호출 계약과 검색 알고리즘을 분리한 `PolicyRetriever` 계층을 설계해 SQL keyword, pgvector, Hybrid RRF, Adaptive fallback 전략을 교체 가능하게 구현하고 기존 챗봇 응답 계약을 유지

## 프로젝트에서 해결한 문제

기존 챗봇은 검색 구현이 툴에 직접 결합돼 있어 검색 방식을 교체하거나
동일 조건으로 비교하기 어려웠다. 또한 “벡터 검색이 더 적합하다”는 판단을
뒷받침할 골드셋과 정량 지표가 없었다.

이를 해결하기 위해 다음 작업을 수행했다.

1. 에이전트와 검색 구현 사이에 공통 리트리버 계층을 추가했다.
2. SQL keyword, Vector, Hybrid, Adaptive 검색을 동일 계약으로 구현했다.
3. 50문항 골드셋과 재현 가능한 평가기를 만들었다.
4. 검색 정확도와 응답 지연시간을 함께 비교해 운영 기본 전략을 결정했다.

## 담당한 핵심 구현

### 리트리버 계층

- `PolicyRetriever` 프로토콜과 정규화된 `RetrievalHit` 모델 도입
- PostgreSQL `ILIKE` 기반 `SqlKeywordPolicyRetriever` 구현
- `text-embedding-3-large`와 pgvector 코사인 거리를 사용하는
  `VectorPolicyRetriever` 구현
- Vector 1.0, SQL 0.25 가중치의 weighted RRF `HybridPolicyRetriever` 구현
- 전체 정책 탐색에서 정책당 최대 2개 청크로 제한해 특정 정책의 긴 문서가
  Top K를 독점하는 현상 완화
- Vector 결과가 비었거나 필요한 근거 역할이 없을 때만 SQL을 조회하는
  `AdaptivePolicyRetriever` 구현
- Adaptive fallback 시 기존 Vector 순위를 보존하고 누락된 역할의 근거만
  보충

### 평가 체계

- 10개 정책을 대상으로 정책별 5문항, 총 50문항 구성
- 정책명을 질문에 직접 노출하지 않고 사용자 상황과 구어체로 변환
- 단일 정답 48문항과 복수 정답 2문항 지원
- 정책 원문, 대체 정답 가능성, 기대 근거 섹션, 자연스러움, 의미 중복을
  전수 점검
- 질문에 정답 정책명이 포함되면 실패하는 회귀 테스트 추가
- Policy Hit@5, Section Hit@5, MRR, p50/p95 latency, error rate,
  Adaptive fallback 비율 측정

## 검증 결과

아래 정확도 값은 동일한 50문항과 `Top K = 5` 조건에서 실행한 benchmark
결과다. latency는 2026년 8월 16일 실제 DB와 OpenAI embedding API로
warm-up 1회, 측정 5회를 실행한 반복 benchmark의 중앙값이다.

| 검색 방식 | Policy Hit@5 | Section Hit@5 | MRR | p50 | p95 |
|---|---:|---:|---:|---:|---:|
| SQL keyword | 18.0% | 12.0% | 0.0957 | 1,396ms | 2,118ms |
| pgvector | 86.0% | 80.0% | 0.7497 | 757ms | 1,181ms |
| Weighted Hybrid RRF | **88.0%** | **80.0%** | **0.7757** | 1,422ms | 2,129ms |
| Adaptive fallback | 86.0% | 80.0% | 0.7497 | 803ms | 2,711ms |

확인된 결과는 다음과 같다.

- pgvector는 단순 SQL keyword 기준선보다 Policy Hit@5가 68%p,
  Section Hit@5가 68%p 높았다.
- Weighted Hybrid는 Vector보다 Policy Hit@5가 2%p, MRR이 0.0260
  높았지만 반복 실행 p50 latency가 약 665ms 길었다.
- Adaptive는 broad 검색에서 7/50문항(14%)만 SQL fallback을 실행하면서
  Vector와 같은 Policy Hit@5 86%, Section Hit@5 80%를 유지했다.
- 실제 챗봇 흐름처럼 정책 ID를 지정한 근거 검색에서는 Vector와 Adaptive
  모두 Section Hit@5 98%였고 Adaptive fallback은 1/50문항(2%)이었다.
- 반복 실행의 error rate는 모든 전략에서 0.0%였고, 실행별 정확도와
  fallback 비율은 변하지 않았다.

따라서 정확도만 보면 Hybrid가 가장 높았지만, 작은 정확도 이득에 비해
지연시간 증가가 컸다. 운영 기본값은 Vector 정상 경로를 유지하는 Adaptive로
정하고, 전체 Hybrid는 검색 누락 비용이 더 큰 별도 흐름에서 선택할 수
있도록 분리했다.

## 기술 스택 키워드

`Python`, `FastAPI`, `PostgreSQL`, `pgvector`, `OpenAI Embeddings`,
`LangGraph Tool Calling`, `asyncio`, `pytest`, `RRF`,
`Retrieval Evaluation`

## 면접용 30초 설명

> 정책 챗봇의 검색 품질을 감으로 판단하지 않기 위해 먼저 검색 구현을 공통 인터페이스로 분리했습니다. 이후 SQL 부분문자열 검색, pgvector 의미 검색, weighted RRF 하이브리드를 50문항 골드셋으로 비교했습니다. 정책 적중뿐 아니라 답변에 필요한 근거 섹션과 순위까지 측정했고, Hybrid가 Policy Hit@5 88%로 가장 높았지만 Vector보다 지연시간이 컸습니다. 그래서 운영 경로에는 Vector를 먼저 사용하고 근거 역할이 누락됐을 때만 SQL을 보충하는 Adaptive fallback을 적용했습니다.

## 예상 면접 질문과 답변

### 왜 Hit@5만 측정하지 않았나?

정답 정책이 Top 5에 있어도 지원 대상, 지원 내용, 신청 방법처럼 실제 답변에
필요한 섹션이 없으면 올바른 근거라고 보기 어렵다. 그래서 정책 단위
Hit@5와 함께 Section Hit@5를 측정했다. MRR은 정답이 포함된 경우에도
얼마나 앞 순위에 배치됐는지를 구분한다.

### 왜 Hybrid를 기본값으로 사용하지 않았나?

Hybrid는 Vector보다 Policy Hit@5가 2%p 높았지만 Section Hit@5는 같았고,
반복 실행 p50 latency는 약 665ms 증가했다. 따라서 모든 요청에서 두 검색을 실행하는
대신 Vector를 정상 경로로 사용하고 근거 역할이 없을 때만 SQL을 보충하도록
했다.

### Adaptive의 성과는 정확도 향상인가?

아니다. 현재 골드셋에서는 Vector보다 정확도가 높아지지 않았다. 검증된
성과는 Vector의 검색 품질을 유지하면서 SQL 추가 조회를 broad 검색 14%,
정책별 근거 검색 2%로 제한한 것이다. Adaptive는 정확도 개선 기법이라기보다
조건부 근거 누락 안전망으로 설명해야 한다.

### 에이전트가 검색 방식을 직접 고르나?

에이전트는 기존과 동일하게 정책 검색 툴을 호출한다. 실제 검색 전략은 툴
내부의 리트리버 팩토리가 결정한다. LLM의 비결정적 판단과 검색 알고리즘
선택을 분리해 테스트와 교체가 가능하도록 했다.

### 골드셋은 신뢰할 수 있나?

정책 원문과 방식별 검색 후보를 대조해 대체 정답, 기대 섹션, 정책명 노출,
질문의 자연스러움과 의미 중복을 전수 점검했다. 두 정책이 모두 정답이 될
수 있는 문항은 복수 정답으로 수정했다. 따라서 이 평가는 정책 원문 기준의
50문항 내부 검색 검증셋으로 설명할 수 있다. 다만 외부 평가자 간 일치도를
측정한 공개 표준 데이터셋은 아니다.

## 이력서에서 피해야 할 표현

- **“RDBMS와 Vector DB를 비교했다”**
  - 네 방식 모두 PostgreSQL을 사용한다. 정확한 표현은 PostgreSQL
    `ILIKE` 기반 keyword 검색과 pgvector 의미 검색 및 결합 방식을 비교했다는
    것이다.
- **“Adaptive로 정확도를 개선했다”**
  - 현재 측정값에서는 Vector와 정확도가 같다. 검색 품질을 유지하면서
    조건부 fallback으로 불필요한 SQL 실행을 줄였다고 써야 한다.
- **“Faithfulness를 개선하거나 검증했다”**
  - 현재 평가는 리트리버가 올바른 정책과 섹션을 찾는지만 측정한다.
    생성 답변이 근거에 충실한지는 아직 평가하지 않았다.
- **“검증된 표준 데이터셋을 사용했다”**
  - 정확한 표현은 "정책 원문 기준으로 검수한 50문항 내부 검색 검증셋"이다.
    독립적인 사람 검수와 평가자 간 일치도 측정은 완료되지 않았다.
- **지연시간을 운영 SLA처럼 표현**
  - 현재 latency는 실제 5회 반복 측정값이지만 API 네트워크, 로컬 DB 상태,
    순차 실행 방식의 영향을 받는다. 이력서의 핵심 성과는 정확도와 fallback
    비율로 두는 편이 안전하다.

## 포트폴리오 확장 시 다음 작업

1. 평가 전용 query embedding cache를 만들어 같은 질문 임베딩을 전략 간 공유
2. embedding/API latency와 DB vector search latency를 분리 측정
3. 정답 답변 또는 원자적 사실 목록을 추가해 생성 답변의 faithfulness 평가
4. 일부 문항을 제3자가 독립 검수하고 평가자 간 일치도 기록
5. PostgreSQL FTS 또는 한국어 형태소 기반 keyword 기준선을 추가해 재비교

## 근거 자료

- 리트리버 구현: `app/ai/retrievers/policy_retriever.py`
- 챗봇 검색 툴: `app/ai/tools/policy_chunk_search_tool.py`
- 평가기: `tests/eval/retrieval_eval.py`
- 50문항 골드셋: `tests/eval/retrieval_cases.jsonl`
- 골드셋 검수 기록: `docs/RETRIEVAL_GOLDSET_AUDIT.md`
- 벤치마크 설명: `docs/RETRIEVAL_BENCHMARK.md`
- 전체 정책 검색 결과: `output/retrieval_benchmark_50.json`
- 정책 범위 지정 결과: `output/retrieval_benchmark_scoped_50.json`
- 반복 전체 정책 검색 결과: `output/retrieval_benchmark_repeated_broad_5.json`
- 반복 정책 범위 지정 결과: `output/retrieval_benchmark_repeated_scoped_5.json`
