# Generated Grounding Live Review

Status: completed manual review on live capture
Source Capture Started At: 2026-08-16T12:19:32+0900

10개 force-refresh live API 응답의 summary 문장과 evidence 문구를
claim 단위로 분해해 수동 grounding rubric으로 검토했다.

## Metrics

| Metric | Value |
| --- | ---: |
| policy_count | 10 |
| claim_count | 65 |
| summary_claim_count | 36 |
| evidence_claim_count | 29 |
| claim_grounding_rate_pct | 100.0 |
| unsupported_claim_count | 0 |
| critical_error_count | 0 |
| display_quality_issue_count | 0 |

## Display Quality Issues

| Claim | Policy | Part | Display Issue | Text |
| --- | ---: | --- | --- | --- |

## Portfolio Safe Claims

- 10개 force-refresh live API 응답의 사용자 표시 claim을 수동 rubric으로 검토했다.
- 검토된 claim은 모두 policy field 또는 retrieved chunk 근거로 지지됐다.
- 이번 재실행 기준 내부 필드명 노출과 오탈자성 표시 품질 이슈는 0건이었다.

## Recommended Improvements

- display quality issue count를 live review regression metric으로 유지
- 새 내부 enum 또는 raw field가 생기면 sanitizer marker와 live review marker를 함께 확장
- structured citation 계약 도입 전까지 live review artifact를 release evidence로 유지
