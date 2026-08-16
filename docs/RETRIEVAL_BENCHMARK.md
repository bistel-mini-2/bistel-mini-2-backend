# 정책 챗봇 리트리버 벤치마크

## 목적

정책 요약 챗봇의 근거 검색을 같은 50문항으로 평가해 다음 네 방식을
정량 비교한다.

1. `sql_keyword`: PostgreSQL `ILIKE` 기반 키워드 일치율 검색
2. `vector`: `text-embedding-3-large` + pgvector 코사인 거리 검색
3. `hybrid`: SQL 키워드와 벡터 결과를 weighted RRF로 결합
4. `adaptive`: Vector를 먼저 실행하고 필요한 근거 역할이 없을 때만
   SQL 결과를 보충

이 평가는 답변 생성 품질이 아니라 **리트리버 품질**을 측정한다.
Faithfulness는 검색된 근거로 생성한 답변을 별도 평가해야 한다.

## 데이터셋

- 실행일: 2026-07-28
- 문항 수: 50개
- 문항 설계 기준: 실제 임베딩이 존재하는 10개 정책
- 복수 정답을 포함한 허용 정답 정책: 12개
- 구성: 정책별 5문항
- 질문 유형: 지원 대상 15, 지원 내용 21, 신청 방법 10, 유의사항 4
- 정답 구성: 단일 정답 48문항, 복수 정답 2문항
- 골드셋: `tests/eval/retrieval_cases.jsonl`
- 전수 검수 기록: `docs/RETRIEVAL_GOLDSET_AUDIT.md`
- 검색 범위: 전체 정책 청크
- Top K: 5

정답은 질문마다 복수 정답을 허용하는 `expected_policy_ids`와
`expected_sections`로 관리한다.
질문에는 정답 정책명을 직접 넣지 않으며, 원문의 대상·금액·기간·기관을
사용자 상황과 구어체 표현으로 바꿨다. 회귀 테스트에서 모든 질문이
정답 정책명을 그대로 포함하지 않는지 검사한다.

## 지표

- Policy Hit@5: 상위 5개 청크에 정답 정책이 한 번 이상 포함된 비율
- Section Hit@5: 정답 정책의 기대 근거 섹션까지 포함된 비율
- MRR: 정답 정책이 처음 등장한 순위의 역수 평균
- p50/p95 latency: 문항별 검색 지연시간의 중앙값과 95백분위
- Error rate: 검색 예외가 발생한 문항 비율

## 결과

| 방식 | Policy Hit@5 | Section Hit@5 | MRR | p50 | p95 | Error |
|---|---:|---:|---:|---:|---:|---:|
| SQL keyword | 18.0% | 12.0% | 0.0957 | 1,257ms | 1,732ms | 0.0% |
| pgvector | 86.0% | **80.0%** | 0.7497 | **782ms** | **1,093ms** | 0.0% |
| Weighted Hybrid RRF | **88.0%** | **80.0%** | **0.7757** | 1,292ms | 1,717ms | 0.0% |
| Adaptive fallback | 86.0% | **80.0%** | 0.7497 | 809ms | 2,290ms | 0.0% |

벡터 검색은 SQL 키워드 대비 Policy Hit@5가 68%p, Section Hit@5가
68%p 높았다. 개선된 하이브리드는 벡터 대비 Policy Hit@5가 2%p,
MRR이 0.0260 높았고 Section Hit@5는 같았다. 대신 p50은 벡터 단독보다
약 510ms 느렸다.

현재 SQL 기준선은 형태소 분석이나 검색어 가중치가 없는 부분문자열
검색이다. 동등 가중치 RRF에서는 큰 참고문서의 일반 단어 일치가 벡터
순위를 오염시켰다. 이를 완화하기 위해 벡터 1.0, SQL 0.25의 가중치를
적용하고 전체 정책 탐색 시 정책당 최대 2개 청크만 Top K에 포함했다.
정책을 지정한 검색은 여러 근거 섹션이 필요하므로 청크 제한을 적용하지
않는다.

현재 하이브리드는 정확도 우선 선택지로 의미가 생겼지만, Policy Hit@5
2%p 개선에 비해 지연시간 증가가 크다.

Adaptive는 Vector 결과가 비었거나 요청한 표준 근거 역할
(`TARGET`, `BENEFIT`, `APPLICATION`, `CAUTION`, `SUMMARY`)이 없을 때만
SQL을 추가 조회한다. 역할 보충 시 기존 Vector 상위 결과를 유지하고
해당 역할 청크만 마지막 자리에 추가하며, 유효한 보충 청크가 없으면
Vector 결과를 그대로 반환한다. 점수 임계값과 정책 다양성은 성공·실패를
구분하지 못하거나 정답 근거를 손상시켜 fallback 조건에서 제외했다.

Broad 평가에서 Adaptive는 7/50문항(14%)에 fallback했고 Vector와 동일한
정확도를 유지했다. p50은 전체 Hybrid보다 낮았지만 p95는 fallback 문항의
추가 SQL 조회로 더 높았다. 한 번의 순차 실행이므로 절대 지연시간보다
fallback 비율과 정확도 보존 여부를 중심으로 해석한다.

## 5회 반복 실행 결과

2026년 8월 16일에 실제 DB와 OpenAI embedding API를 사용해 warm-up 1회,
측정 5회 반복 실행을 완료했다. 대표 latency는 warm-up을 제외한 실행별
p50/p95의 중앙값이다. 모든 전략의 error rate는 0.0%였고, 실행별 정확도와
fallback 비율은 변하지 않았다.

### Broad 반복 실행

| 방식 | Policy Hit@5 | Section Hit@5 | MRR | p50 median | p95 median | Fallback |
|---|---:|---:|---:|---:|---:|---:|
| SQL keyword | 18.0% | 12.0% | 0.0957 | 1,396ms | 2,118ms | - |
| pgvector | 86.0% | 80.0% | 0.7497 | 757ms | 1,181ms | - |
| Weighted Hybrid RRF | 88.0% | 80.0% | 0.7757 | 1,422ms | 2,129ms | - |
| Adaptive fallback | 86.0% | 80.0% | 0.7497 | 803ms | 2,711ms | 14.0% |

### Scoped 반복 실행

| 방식 | Section Hit@5 | p50 median | p95 median | Fallback |
|---|---:|---:|---:|---:|
| pgvector | 98.0% | 662ms | 972ms | - |
| Adaptive fallback | 98.0% | 673ms | 1,028ms | 2.0% |

반복 실행 기준으로도 Hybrid는 pgvector보다 Policy Hit@5가 2%p 높지만,
p50 latency는 약 665ms, p95 latency는 약 948ms 증가했다. Adaptive는
pgvector와 같은 정확도를 유지하고 fallback을 14.0%로 제한했지만, broad
p95는 fallback 문항의 추가 SQL 조회로 높다.

## 정책 범위 지정 근거 검색

실제 챗봇처럼 정책 ID를 이미 알고 해당 정책의 근거 섹션을 찾는 조건도
별도로 평가했다. 이 모드의 Policy Hit은 검색 범위를 정답 정책으로
제한했기 때문에 성능 비교에 사용하지 않고 Section Hit만 해석한다.

| 방식 | Section Hit@5 | p50 | p95 | Fallback |
|---|---:|---:|---:|---:|
| pgvector | 98.0% | 665ms | 1,027ms | - |
| Adaptive fallback | 98.0% | 700ms | 951ms | 2.0% (1/50) |

현재 골드셋에서는 Adaptive가 추가 정답을 만들지는 않았지만, 98%의 섹션
적중률을 유지하면서 SQL 추가 조회를 1문항으로 제한했다. 따라서 Adaptive는
정확도 향상 수치가 아니라 근거 누락 시에만 동작하는 안전망으로 해석한다.

## 실행 방법

```bash
PYTHONPATH=. .venv/bin/python tests/eval/retrieval_eval.py \
  --top-k 5 \
  --output output/retrieval_benchmark_50.json
```

특정 전략이나 문항만 다시 실행할 수 있다.

```bash
PYTHONPATH=. .venv/bin/python tests/eval/retrieval_eval.py \
  --strategies vector hybrid \
  --ids R001 R002 R003
```

반복 지연시간을 측정할 때는 warm-up 실행과 실제 측정 실행을 분리한다.
`--warmup-runs` 결과는 JSON에 보존하지만 대표 p50/p95 계산에서는 제외한다.
`--repeat-runs 1`과 `--warmup-runs 0`의 기본 실행은 기존 단일 실행 JSON shape를
유지한다. `--repeat-runs`가 2 이상이거나 warm-up이 있으면 반복 실행 요약에
`execution_mode`, `run_count`, `warmup_run_count`, `total_run_count`,
`strategies_order`, 실행별 metric 배열, 중앙값 요약, `warmup_runs`, `runs`를
기록한다.
외부 embedding API 응답 대기가 길어지는 경우에는 `--case-timeout-seconds`로
문항 단위 timeout을 걸 수 있다. Timeout은 실패 문항의 `error`와 전략별
`error_rate_pct`에 기록되며, 성공 수치로 대체하지 않는다.
긴 반복 실행은 `--checkpoint-output`으로 완료된 전략 단위 결과를 JSONL에
남길 수 있다. 최종 JSON이 생성되기 전에 실행을 중단해도 checkpoint로
완료된 구간을 확인한다.

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

정책 범위 지정 근거 검색은 다음처럼 실행한다.

```bash
PYTHONPATH=. .venv/bin/python tests/eval/retrieval_eval.py \
  --strategies vector adaptive \
  --scope-to-expected-policy \
  --output output/retrieval_benchmark_scoped_50.json
```

Scoped 반복 실행은 다음처럼 별도 파일에 저장한다.

```bash
PYTHONPATH=. .venv/bin/python tests/eval/retrieval_eval.py \
  --strategies vector adaptive \
  --scope-to-expected-policy \
  --warmup-runs 1 \
  --repeat-runs 5 \
  --case-timeout-seconds 15 \
  --top-k 5 \
  --checkpoint-output output/retrieval_benchmark_repeated_scoped_5.checkpoint.jsonl \
  --output output/retrieval_benchmark_repeated_scoped_5.json
```

## Benchmark 실행 병목과 개선 방향

반복 benchmark가 오래 걸리는 주된 이유는 같은 질문 임베딩을 전략별,
실행별로 재생성하기 때문이다. Broad 반복 실행은 warm-up 포함 6회 × 50문항
× vector 기반 3전략으로 약 900회 embedding API 호출을 만들고, Scoped 반복
실행은 약 600회를 추가한다. 각 embedding 뒤에는 pgvector 검색도 순차 실행된다.

이번 보강으로 `--case-timeout-seconds`와 `--checkpoint-output`을 추가해
장기 대기와 중간 결과 손실을 줄였다. 다음 개선은 평가 전용 query embedding
cache, embedding/API latency와 DB search latency의 분리 측정, checkpoint에서
최종 요약을 재구성하는 resume 기능, SQL keyword 기준선의 FTS 또는 trigram
index 전환이다.

## 재현성 및 해석 범위

- 원본 실행 결과는 `output/retrieval_benchmark_50.json`에 저장한다.
- 정책 범위 지정 결과는
  `output/retrieval_benchmark_scoped_50.json`에 저장한다.
- 반복 실행 결과는 `output/retrieval_benchmark_repeated_broad_5.json`,
  `output/retrieval_benchmark_repeated_scoped_5.json`에 저장한다.
- 임베딩 API 네트워크 상태와 DB 부하에 따라 지연시간은 달라질 수 있다.
- 단일 실행 결과와 반복 실행 결과를 구분한다. 이력서나 포트폴리오의
  latency 수치는 워밍업을 제외한 반복 실행 중앙값을 사용한다.
- 데이터셋은 현재 정책 데이터에 맞춘 내부 골드셋이다. 정책 데이터나
  임베딩 모델이 바뀌면 다시 실행해야 한다.
