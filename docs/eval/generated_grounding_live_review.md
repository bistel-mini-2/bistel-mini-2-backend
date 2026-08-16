# Generated Grounding Live Review

Status: completed manual review on live capture
Source Capture Started At: 2026-08-16T12:15:11+0900

10개 force-refresh live API 응답의 summary 문장과 evidence 문구를
claim 단위로 분해해 수동 grounding rubric으로 검토했다.

## Metrics

| Metric | Value |
| --- | ---: |
| policy_count | 10 |
| claim_count | 62 |
| summary_claim_count | 32 |
| evidence_claim_count | 30 |
| claim_grounding_rate_pct | 100.0 |
| unsupported_claim_count | 0 |
| critical_error_count | 0 |
| display_quality_issue_count | 3 |

## Display Quality Issues

| Claim | Policy | Part | Display Issue | Text |
| --- | ---: | --- | --- | --- |
| L238-06 | 238 | evidence | internal_marker | 신청 기간은 수시이고, 방문 방식(OFFLINE_ONLY)입니다. |
| L251-05 | 251 | summary | malformed_text | 주요 지원 내용은 인플루엔자 예방접종 1회를 지원합니이에요. |
| L251-08 | 251 | evidence | internal_marker | 신청은 OFFLINE_ONLY이며, 담당 시/군/구청·보건소·인플루엔자 위탁 의료기관에서 서비스를 제공합니다. |

## Portfolio Safe Claims

- 10개 force-refresh live API 응답의 사용자 표시 claim을 수동 rubric으로 검토했다.
- 검토된 claim은 모두 policy field 또는 retrieved chunk 근거로 지지됐다.
- 다만 내부 필드명 노출과 오탈자성 문장 품질 이슈는 별도 개선 대상으로 남았다.

## Recommended Improvements

- fallback summary phrase의 '합니이 주요 지원 대상이에요' 오탈자 경로 수정
- evidence sanitizer가 application_status/application_period_text 같은 내부 필드명을 제거하도록 확장
- structured citation 계약 도입 전까지 live review artifact를 release evidence로 유지
