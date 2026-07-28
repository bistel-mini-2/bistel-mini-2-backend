# 정책 챗봇 리트리버 벤치마크

## 목적

정책 요약 챗봇의 근거 검색을 같은 50문항으로 평가해 다음 세 방식을
정량 비교한다.

1. `sql_keyword`: PostgreSQL `ILIKE` 기반 키워드 일치율 검색
2. `vector`: `text-embedding-3-large` + pgvector 코사인 거리 검색
3. `hybrid`: SQL 키워드와 벡터 결과를 RRF(Reciprocal Rank Fusion)로 결합

이 평가는 답변 생성 품질이 아니라 **리트리버 품질**을 측정한다.
Faithfulness는 검색된 근거로 생성한 답변을 별도 평가해야 한다.

## 데이터셋

- 실행일: 2026-07-28
- 문항 수: 50개
- 정답 정책: 실제 임베딩이 존재하는 10개 정책
- 구성: 정책별 5문항
- 질문 유형: 지원 대상 15, 지원 내용 21, 신청 방법 10, 유의사항 4
- 골드셋: `tests/eval/retrieval_cases.jsonl`
- 검색 범위: 전체 정책 청크
- Top K: 5

정답은 질문마다 `expected_policy_id`와 `expected_sections`로 관리한다.
정책명 그대로 찾는 질문뿐 아니라 대상·금액·기간·신청기관을 자연어로
바꾼 질문을 포함한다.

## 지표

- Policy Hit@5: 상위 5개 청크에 정답 정책이 한 번 이상 포함된 비율
- Section Hit@5: 정답 정책의 기대 근거 섹션까지 포함된 비율
- MRR: 정답 정책이 처음 등장한 순위의 역수 평균
- p50/p95 latency: 문항별 검색 지연시간의 중앙값과 95백분위
- Error rate: 검색 예외가 발생한 문항 비율

## 결과

| 방식 | Policy Hit@5 | Section Hit@5 | MRR | p50 | p95 | Error |
|---|---:|---:|---:|---:|---:|---:|
| SQL keyword | 76.0% | 68.0% | 0.684 | 1,355ms | 1,861ms | 0.0% |
| pgvector | 90.0% | 84.0% | 0.849 | 808ms | 1,439ms | 0.0% |
| Hybrid RRF | **96.0%** | **92.0%** | **0.900** | 1,245ms | 1,742ms | 0.0% |

하이브리드는 SQL 키워드 대비 Policy Hit@5가 20%p, Section Hit@5가
24%p 높았다. 벡터 단독 대비로도 각각 6%p, 8%p 높았다. 지연시간은
벡터 단독이 가장 낮았고, 하이브리드는 정확도를 높이는 대신 p50 기준
약 437ms가 추가됐다.

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

## 재현성 및 해석 범위

- 원본 실행 결과는 `output/retrieval_benchmark_50.json`에 저장한다.
- 임베딩 API 네트워크 상태와 DB 부하에 따라 지연시간은 달라질 수 있다.
- 현재 결과는 한 번의 50문항 실행 결과다. 지연시간을 이력서 수치로
  사용할 때는 워밍업 후 3회 이상 반복 실행한 중앙값을 권장한다.
- 데이터셋은 현재 정책 데이터에 맞춘 내부 골드셋이다. 정책 데이터나
  임베딩 모델이 바뀌면 다시 실행해야 한다.
