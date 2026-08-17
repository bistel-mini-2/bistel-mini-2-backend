# Dodam 검색 품질 근거 보강 리포트

## 평가 목적

Dodam 정책 챗봇 작업을 "RAG 챗봇을 만들었다"가 아니라, 검색 품질을
측정하고 현재 서비스 조건에 맞는 검색 기본값 후보를 선택한 case study로
설명하기 위한 근거를 정리한다.

이 문서는 리트리버 평가 근거를 중심으로 다룬다. 생성 답변 grounding은
검색 evidence correctness와 같은 지표로 합치지 않고,
`docs/eval/generated_grounding_live_review.md`에 별도 산출물로 분리했다.

## 변경 사항

- 커밋 `c0b0336`에서 retrieval goldset을 "정책 원문 기준으로 검수한
  50문항 내부 검색 검증셋"으로 정리했다.
- `R030`은 비공개 상담 가능 여부를 묻는 문항이므로 `expected_sections`에
  `지원 내용`을 추가했다. 대상성은 `공식 지원대상 원문`, 비밀상담 가능
  여부는 `지원 내용`을 근거로 본다.
- 기존 benchmark JSON은 새 검색 호출 없이 저장된 `retrieved` 결과에
  수정된 gold label을 재적용해 재채점했다. Broad 평가의 pgvector,
  Weighted Hybrid RRF, Adaptive fallback Section Hit@5는 80.0%로 정리됐다.
- 질문 유형 taxonomy, fallback 사례, evidence correctness, 전략 선택 요약,
  실제 5회 반복 latency 산출물을 추가해 포트폴리오에서 설명 가능한 근거를
  분리했다.
- 이 문서는 공개 표준 데이터셋 검증이 아니라 내부 검색 검증셋 기반
  retrieval evidence 정리로 한정한다.

## 평가 데이터와 골드셋 범위

- 골드셋: `tests/eval/retrieval_cases.jsonl`
- 문항 수: 50개
- 기준 정책: 10개 정책, 정책별 5문항
- 허용 정답 정책: 복수 정답 2문항을 포함해 12개 policy id
- Top K: 5
- 정답 라벨: `expected_policy_ids`, `expected_sections`
- 문항 검수 기록: `docs/RETRIEVAL_GOLDSET_AUDIT.md`

골드셋은 정책 원문 기준으로 검수한 50문항 내부 검색 검증셋이다.
정책명을 질문에 직접 노출하지 않도록 구성했고, 정답 정책과 근거 섹션을
분리해 라벨링했다. 다만 외부 평가자 간 일치도를 측정한 공개 표준
벤치마크는 아니다.

## Broad와 Scoped 조건

Broad 평가는 사용자 자연어 질문만으로 전체 정책 청크에서 정답 정책과 근거
섹션을 찾는 조건이다. 결과 파일은 `output/retrieval_benchmark_50.json`이다.

Scoped 평가는 실제 정책 요약 흐름처럼 이미 `policy_id`를 알고 있는 상태에서
해당 정책 안의 답변 근거 섹션을 찾는 조건이다. `policy_summary_graph`는
정책 요약 전 `search_policy_chunks`에 `policy_ids=[policy["policy_id"]]`를
넘긴다. 따라서 scoped 평가는 정책 발견 성능이 아니라, 답변에 쓸 섹션
검색 품질을 해석하는 데 사용한다. 결과 파일은
`output/retrieval_benchmark_scoped_50.json`이다.

## 검색 전략별 결과 요약

### Broad 전체 정책 검색

| 전략 | Policy Hit@5 | Section Hit@5 | MRR | p50 | p95 | Fallback |
|---|---:|---:|---:|---:|---:|---:|
| SQL keyword | 18.0% | 12.0% | 0.0957 | 1,257ms | 1,732ms | - |
| pgvector | 86.0% | 80.0% | 0.7497 | 782ms | 1,093ms | - |
| Weighted Hybrid RRF | 88.0% | 80.0% | 0.7757 | 1,292ms | 1,717ms | - |
| Adaptive fallback | 86.0% | 80.0% | 0.7497 | 809ms | 2,290ms | 7/50, 14.0% |

### Scoped 정책 범위 근거 검색

| 전략 | Policy Hit@5 | Section Hit@5 | MRR | p50 | p95 | Fallback |
|---|---:|---:|---:|---:|---:|---:|
| pgvector | 100.0% | 98.0% | 1.0000 | 665ms | 1,027ms | - |
| Adaptive fallback | 100.0% | 98.0% | 1.0000 | 700ms | 951ms | 1/50, 2.0% |

## 반복 Latency 결과

2026년 8월 16일에 실제 DB와 OpenAI embedding API를 사용해 warm-up 1회,
측정 5회 반복 benchmark를 실행했다. 기존 1회 benchmark 파일은 덮어쓰지
않고 Broad/Scoped 반복 결과를 별도 JSON으로 보존했다.

```bash
PYTHONPATH=. .venv/bin/python tests/eval/retrieval_eval.py \
  --strategies sql_keyword vector hybrid adaptive \
  --warmup-runs 1 \
  --repeat-runs 5 \
  --case-timeout-seconds 15 \
  --top-k 5 \
  --checkpoint-output output/retrieval_benchmark_repeated_broad_5.checkpoint.jsonl \
  --output output/retrieval_benchmark_repeated_broad_5.json
```

반복 결과는 `output/retrieval_latency_repeated.json`에
`actual_repeated_run_available=true`로 요약했다. 대표 latency는 warm-up을
제외한 실행별 p50/p95의 중앙값이다.

| 조건 | 전략 | Policy Hit@5 | Section Hit@5 | p50 median | p95 median | Error | Fallback |
|---|---|---:|---:|---:|---:|---:|---:|
| broad | SQL keyword | 18.0% | 12.0% | 1,396ms | 2,118ms | 0.0% | - |
| broad | pgvector | 86.0% | 80.0% | 757ms | 1,181ms | 0.0% | - |
| broad | Hybrid | 88.0% | 80.0% | 1,422ms | 2,129ms | 0.0% | - |
| broad | Adaptive | 86.0% | 80.0% | 803ms | 2,711ms | 0.0% | 14.0% |
| scoped | pgvector | 100.0% | 98.0% | 662ms | 972ms | 0.0% | - |
| scoped | Adaptive | 100.0% | 98.0% | 673ms | 1,028ms | 0.0% | 2.0% |

반복 실행에서도 정확도와 fallback 비율은 실행별로 변하지 않았다. 다만
Adaptive broad p95는 fallback 문항의 추가 SQL 조회 영향으로 pgvector보다
크다.

## 질문 유형별 결과

원본 골드셋을 직접 수정하지 않고 `tests/eval/retrieval_case_taxonomy.json`에
별도 taxonomy를 만들었다.

| 유형 | 문항 수 | 기준 |
|---|---:|---|
| direct_policy_name | 0 | 정책명을 직접 말하는 질문 |
| condition_missing | 10 | 기한, 예외, 제한 조건, 누락 조건을 묻는 질문 |
| similar_policy | 15 | 유사 정책이 많은 도메인에서 정답 정책 구분이 필요한 질문 |
| ambiguous_question | 2 | 검수된 복수 정답 또는 모호성이 있는 질문 |
| eligibility_or_rule | 10 | 대상 자격, 조건 구조, 규칙 중심 질문 |
| other | 13 | 단순 혜택 또는 신청 경로 중심 질문 |

유형별 성능 요약은 `output/retrieval_case_taxonomy_summary.json`에 저장했다.
Broad 평가에서 pgvector는 ambiguous 2문항을 모두 맞췄고, condition_missing
유형에서는 Section Hit@5 80.0%, eligibility_or_rule 유형에서도 80.0%였다.
Hybrid는 condition_missing 유형 Section Hit@5가 100.0%로 높았지만,
eligibility_or_rule과 other 유형에서는 pgvector보다 낮은 섹션 적중 사례가
있었다.

## Adaptive Fallback 사례 요약

Adaptive fallback은 vector 결과가 비었거나 요청한 evidence role이 top-k에
없을 때만 SQL keyword 검색을 추가 실행한다.

- Broad: 7/50문항, 14.0%
- Scoped: 1/50문항, 2.0%
- 상세 사례: `output/retrieval_fallback_cases.json`

Broad fallback 발동 문항은 `R002`, `R006`, `R008`, `R010`, `R022`, `R039`,
`R050`이다. 이 중 `R010`, `R022`, `R050`은 기대 정책과 섹션을 포함했고,
`R006`은 기대 정책은 포함했지만 기대 섹션이 부족했다. `R002`, `R008`,
`R039`는 기대 정책 자체가 top-k에 없어 답변 품질 리스크로 남는다.

Scoped fallback은 `R015` 1문항에서만 발동했고, 기대 정책과 기대 섹션을
포함했다.

## Evidence Correctness 평가 결과

`tests/eval/retrieval_portfolio_evidence.py`는 기존 benchmark 결과를 바탕으로
검색된 top-k 근거가 답변에 사용할 수 있는지 평가한다. 평가 이름은
`retrieved_evidence_correctness`이며, 생성 답변 faithfulness와 구분한다.

| 조건 | 전략 | Policy Evidence Hit | Section Evidence Hit | Answer-ready Evidence | Risk Count |
|---|---|---:|---:|---:|---:|
| broad | SQL keyword | 18.0% | 12.0% | 12.0% | 44 |
| broad | pgvector | 86.0% | 80.0% | 80.0% | 10 |
| broad | Hybrid | 88.0% | 80.0% | 80.0% | 10 |
| broad | Adaptive | 86.0% | 80.0% | 80.0% | 10 |
| scoped | pgvector | 100.0% | 98.0% | 98.0% | 1 |
| scoped | Adaptive | 100.0% | 98.0% | 98.0% | 1 |

검색 실패가 답변 품질 리스크로 이어지는 경로는 단순하다. 기대 정책이 없으면
답변이 다른 정책을 근거로 삼을 수 있고, 기대 정책은 있어도 기대 섹션이
없으면 대상, 혜택, 신청 방법, 유의사항 중 필요한 근거가 빠진 답변이 될 수
있다. 상세 risk case는 `output/retrieval_evidence_quality.json`에 저장했다.

## 생성 답변 Grounding 현황

검색 근거 correctness와 생성 답변 grounding은 별도 산출물로 관리한다.
2026년 8월 16일 force-refresh live 실행에서는 실제 DB, retriever, policy
summary generator 경로로 10개 정책 요약 API 응답을 캡처했고, 사용자 표시
summary/evidence를 claim 단위로 분해해 수동 rubric으로 검토했다.

| 산출물 | 상태 | 핵심 결과 |
|---|---|---|
| `output/generated_grounding_live_api_capture.json` | actual live capture | 10/10 응답 `done`, 내부 marker 0건 |
| `output/generated_grounding_live_review.json` | completed manual review | 65개 claim, grounding 100.0%, unsupported 0, critical error 0, display quality issue 0 |

이 결과는 "캡처된 10개 live 응답 claim을 수동 rubric으로 검토했다"는
근거로 사용할 수 있다. 다만 런타임 API가 문장별 structured citation을
제공한다는 뜻은 아니다.

## Hybrid 기본값 미채택 근거

현재 50문항 내부 평가 조건에서는 Hybrid가 pgvector보다 Policy Hit@5가
2.0%p, MRR이 0.0260 높다. 그러나 Section Hit@5는 80.0%로 같고, p50
latency는 단일 실행 기준 약 510ms, 반복 실행 중앙값 기준 약 665ms
증가했다. p95 latency도 반복 실행 중앙값 기준 약 948ms 증가했다.

Adaptive는 broad에서 pgvector와 같은 Policy Hit@5 86.0%, Section Hit@5
80.0%를 유지하면서 SQL fallback을 7/50문항으로 제한했다. Scoped에서도
Section Hit@5 98.0%를 유지하고 fallback은 1/50문항이었다.

따라서 현재 평가 조건에서는 모든 요청에서 Hybrid를 실행하기보다, Vector를
정상 경로로 두고 근거 역할이 부족할 때만 SQL을 보충하는 Adaptive를 챗봇
기본 검색 전략 후보로 보는 것이 더 보수적이다. 이 판단은
`output/retrieval_strategy_decision_summary.json`에 자동 요약했다.

## 반복 실행 병목과 개선사항

반복 benchmark가 오래 걸린 근본 원인은 같은 50개 질문을 전략별, 실행별로
다시 임베딩하고 각 결과마다 vector DB 검색을 순차 수행했기 때문이다. Broad는
warm-up 포함 6회 × 50문항 × vector 기반 3전략으로 약 900회 embedding
호출이 발생했고, Scoped는 약 600회가 추가됐다.

이번 보강에서는 문항 단위 timeout과 전략 단위 checkpoint를 추가했다. 다음
개선은 평가 전용 query embedding cache를 만들어 같은 질문 임베딩을 전략 간
공유하고, embedding latency와 DB vector search latency를 분리 측정하는 것이다.
SQL keyword 기준선은 현재 `ILIKE '%keyword%'` 중심이라 PostgreSQL FTS 또는
trigram index 기준선으로 바꿔 재비교하는 편이 좋다.

## 한계

- Latency는 실제 5회 반복 측정값이지만 OpenAI embedding API, 로컬 DB 상태,
  순차 실행 방식의 영향을 받으므로 운영 SLA가 아니다.
- 골드셋은 정책 원문 기준으로 검수한 50문항 내부 검색 검증셋이며 공개
  표준 데이터셋이 아니다.
- 검색 평가는 검색된 근거의 correctness를 본다. 생성 답변 grounding은
  별도 live review artifact로 분리했으며, runtime structured citation
  계약은 아직 없다.
- 이 문서는 팀 프로젝트 결과와 개인 후속 평가를 구분한다. 팀 결과는
  정책 챗봇 및 검색 구조 구현이고, 이 문서의 taxonomy, fallback 사례 요약,
  evidence correctness, 전략 판단 요약, 생성 grounding live review는
  포트폴리오 근거 강화를 위한 개인 후속 평가 산출물이다.

## 재현 명령

기존 benchmark 재실행:

```bash
PYTHONPATH=. .venv/bin/python tests/eval/retrieval_eval.py \
  --top-k 5 \
  --output output/retrieval_benchmark_50.json
```

Scoped benchmark 재실행:

```bash
PYTHONPATH=. .venv/bin/python tests/eval/retrieval_eval.py \
  --strategies vector adaptive \
  --scope-to-expected-policy \
  --output output/retrieval_benchmark_scoped_50.json
```

포트폴리오 근거 산출물 재생성:

```bash
PYTHONPATH=. python3 tests/eval/retrieval_portfolio_evidence.py
```

생성 답변 grounding 산출물 재생성:

```bash
PYTHONPATH=. .venv/bin/python tests/eval/generated_grounding_live_capture.py
PYTHONPATH=. .venv/bin/python tests/eval/generated_grounding_live_review.py
```
