# Generated Grounding Manual Rubric

Status: manual sample review
Date: 2026-08-16

이 산출물은 생성 답변 grounding을 retrieval benchmark와 분리해
claim 단위로 검토하기 위한 수동 rubric 결과다. DB, retrieval API,
LLM을 호출하지 않는 offline artifact이며 live API faithfulness 주장이 아니다.
현재 end-to-end live grounding 근거는
`docs/eval/generated_grounding_live_review.md`를 따른다.

## Metrics

| Metric | Value |
| --- | ---: |
| sample_count | 10 |
| checkable_claim_count | 9 |
| claim_grounding_rate_pct | 77.8 |
| unsupported_claim_count | 1 |
| critical_error_count | 1 |
| generic_evidence_rate_pct | 10.0 |

## Samples

| Sample | Case | Policy | Status | Issue | Support |
| --- | --- | ---: | --- | --- | --- |
| G001 | R001 | 237 | grounded | none | condition_profile_target_summary, 23790000002, 23790000000 |
| G002 | R002 | 237 | grounded | none | 23790000003 |
| G003 | R004 | 237 | grounded | none | application_method, 23790000004 |
| G004 | R006 | 238 | grounded | none | condition_profile_target_summary, 23890000000, 23890000003 |
| G005 | R010 | 238 | partially_grounded | overgeneralized | application_period_text, 23890000006 |
| G006 | R012 | 240 | grounded | none | name, benefit_description |
| G007 | R021 | 246 | not_checkable | generic_only | - |
| G008 | R031 | 251 | grounded | none | condition_profile_target_summary, 25190000000, 25190000003 |
| G009 | R041 | 289 | grounded | none | benefit_description, 28990000004 |
| G010 | R047 | 292 | unsupported | wrong_amount | benefit_description, 29290000004 |

## Portfolio Safe Claims

- 생성 답변 grounding은 retrieval benchmark와 별도 rubric으로 분리했다.
- 현재 산출물은 offline manual sample review이며 live API faithfulness는
  `docs/eval/generated_grounding_live_review.md`에서 별도 완료했다.
- 문장별 citation 계약은 아직 API/cache/frontend에 구현하지 않았다.

## Next Step

현재 live API summary response 10건에 대한 claim 단위 review는 완료했다.
다음 단계는 이 수동 review를 structured citation 계약 또는 원자적 사실 목록
기반 평가로 확장하는 것이다.
