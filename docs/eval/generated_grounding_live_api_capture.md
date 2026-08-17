# Generated Grounding Live API Capture

Status: live_response_capture_review_completed
Started At: 2026-08-16T12:19:32+0900

이 산출물은 실제 DB, retriever, policy summary generator를 사용해
정책 요약 API 응답 모델을 캡처한 live artifact다.
claim 단위 manual grounding review는
`docs/eval/generated_grounding_live_review.md`에서 완료했다. 이 파일은
live 응답 캡처 근거이며, claim별 grounding 판정의 대표 근거는 review
산출물을 따른다.

## Metrics

| Metric | Value |
| --- | ---: |
| requested_policy_count | 10 |
| completed_response_count | 10 |
| internal_marker_count | 0 |
| all_completed | True |
| all_clean_display_text | True |
| average_elapsed_ms | 2484.247 |

## Records

| Policy | Slug | Status | Lines | Evidence | Elapsed ms |
| ---: | --- | --- | ---: | ---: | ---: |
| 237 | WLF00006317 | done | 2 | 3 | 3498.239 |
| 238 | WLF00006311 | done | 3 | 3 | 1945.278 |
| 240 | WLF00006313 | done | 3 | 3 | 2621.221 |
| 242 | WLF00006308 | done | 3 | 3 | 2076.553 |
| 246 | WLF00006292 | done | 3 | 3 | 1991.548 |
| 247 | WLF00006289 | done | 3 | 3 | 2275.506 |
| 251 | WLF00003213 | done | 3 | 2 | 2884.508 |
| 255 | WLF00004656 | done | 2 | 3 | 2043.635 |
| 289 | WLF00000072 | done | 3 | 3 | 2830.042 |
| 292 | WLF00000076 | done | 3 | 3 | 2675.936 |

## Boundary

- Proves: The live DB/retriever/generator path produced API response models for the selected policies.
- Grounding review: `docs/eval/generated_grounding_live_review.md` scored 65 captured claims.
- Does not prove: Structured citation is available in the runtime API contract.
- Next: Keep live capture and live review artifacts in sync when summary generation or sanitization changes.
