# Chat Evaluation Reliability Design

## Goal

챗봇 live 평가가 시나리오에 선언된 관측 가능한 기대값을 빠짐없이 검증하고, 일부 기대값이 누락된 결과를 E2E 성공으로 잘못 집계하지 않게 한다.

## Approach

프로덕션 라우팅 코드는 변경하지 않는다. `tests/eval/live_eval.py`에서 실제 결과로 관측 가능한 1차 의도, 2차 의도, 응답 타입, clarification 상태, 프로필 추출 값을 각각 비교하고, 모든 필수 비교가 성공한 실행만 strict E2E 성공으로 집계한다.

`expected_handler`는 현재 live 실행 결과에 handler 이름이 노출되지 않아 직접 검증할 수 없다. intent에서 handler를 역추론하면 잘못된 확신을 만들기 때문에 strict E2E 조건에서는 제외하고 결과에 `handler_accuracy_pct: null`과 미측정 사유를 명시한다. Mock 테스트는 기존처럼 실제 패치 지점과 payload를 통해 라우팅을 검증한다.

## Clarification Detection

단순 `text` 응답을 clarification으로 인정하지 않는다. 실행 상태의 `pending.kind == "clarification"`, `policy_selection`, `slot_request`, `profile_confirm` 중 하나가 존재할 때만 clarification으로 판정한다. 기대값이 false인 시나리오에서 불필요한 clarification이 발생해도 strict E2E는 실패한다.

## Metrics

- primary intent accuracy
- secondary intent exact-match accuracy
- response type accuracy
- clarification accuracy
- profile extraction field accuracy
- strict E2E success rate
- error rate
- consistency rate
- handler accuracy: 측정 불가 상태를 명시

오류 실행은 strict E2E 분모에 포함한다. 따라서 오류가 많아도 성공률이 과대평가되지 않는다.

## Verification

평가기 자체 단위 테스트를 추가해 다음을 검증한다.

- 2차 의도 누락 또는 불필요한 추가는 strict E2E 실패
- 일반 text 응답은 clarification 실패
- 명시적 pending clarification은 성공
- 프로필 기대값 불일치는 strict E2E 실패
- 오류 실행은 strict E2E 성공률을 낮춤
- 기존 46개 평가 테스트와 전체 테스트가 통과

## Non-goals

- 실제 LLM 품질 개선
- 프로덕션 응답 스키마 변경
- handler 추적용 런타임 계측 추가
