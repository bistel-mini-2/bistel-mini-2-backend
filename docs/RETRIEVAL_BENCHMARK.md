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
| SQL keyword | 18.0% | 12.0% | 0.0957 | 1,362ms | 2,189ms | 0.0% |
| pgvector | **82.0%** | **74.0%** | **0.7070** | **796ms** | **1,500ms** | 0.0% |
| Hybrid RRF | **82.0%** | **74.0%** | 0.6600 | 1,383ms | 1,827ms | 0.0% |

벡터 검색은 SQL 키워드 대비 Policy Hit@5가 64%p, Section Hit@5가
62%p 높았다. 하이브리드는 Hit@5를 더 높이지 못했고 MRR은 벡터보다
0.047 낮았다. p50도 벡터 단독보다 약 587ms 느렸다.

현재 SQL 기준선은 형태소 분석이나 검색어 가중치가 없는 부분문자열
검색이다. 큰 참고문서의 일반 단어 일치가 상위 순위를 차지하면서 RRF에도
노이즈를 전달했다. 따라서 현재 데이터와 결합 방식에서는 벡터 단독을
기본값으로 유지하는 편이 낫다. 하이브리드를 개선하려면 정책 상세 청크
가중치, 한국어 토큰화, 검색 채널별 RRF 가중치를 별도로 조정해야 한다.

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
