# Generated Grounding Live API Capture

Status: live_response_capture_requires_manual_review
Started At: 2026-08-16T11:46:48+0900

이 산출물은 실제 DB의 현재 policy summary cache를 통해
정책 요약 API 응답 모델을 캡처한 cached live artifact다.
이번 실행은 OpenAI 재생성이나 retriever 재검색을 수행하지 않았다.
다만 claim 단위 manual grounding review는 아직 완료하지 않았으므로 faithfulness
검증 완료로 해석하지 않는다.

## Metrics

| Metric | Value |
| --- | ---: |
| requested_policy_count | 10 |
| completed_response_count | 3 |
| internal_marker_count | 0 |
| all_completed | False |
| all_clean_display_text | True |
| average_elapsed_ms | 11.291 |

## Records

| Policy | Slug | Status | Lines | Evidence | Elapsed ms |
| ---: | --- | --- | ---: | ---: | ---: |
| 237 | WLF00006317 | loading | 0 | 0 | 17.422 |
| 238 | WLF00006311 | loading | 0 | 0 | 12.345 |
| 240 | WLF00006313 | loading | 0 | 0 | 12.614 |
| 242 | WLF00006308 | loading | 0 | 0 | 11.918 |
| 246 | WLF00006292 | loading | 0 | 0 | 10.835 |
| 247 | WLF00006289 | done | 2 | 1 | 9.831 |
| 251 | WLF00003213 | loading | 0 | 0 | 8.872 |
| 255 | WLF00004656 | done | 2 | 1 | 9.365 |
| 289 | WLF00000072 | loading | 0 | 0 | 9.547 |
| 292 | WLF00000076 | done | 3 | 1 | 10.162 |

## Boundary

- Proves: The local DB cache currently returns API response models for the selected policies without a new LLM generation run.
- Does not prove: Manual claim-level grounding has not yet been completed for each captured live response.
- Next: Split each live summary/evidence response into claims and score it with generated_grounding_manual_rubric.py.
