# Chat Request Lifecycle

## 저장 경계

채팅 SSE 요청은 먼저 사용자 메시지와 `chat_request.status = processing`을 저장하고 커밋한다.
Handler 실행 중에는 DB 트랜잭션을 열어두지 않는다.

Handler 성공 후에는 다음 항목을 하나의 트랜잭션으로 저장한다.

- assistant message
- message policy links
- message evidences
- `chat_session.slot_json`
- `chat_request.status = completed`
- `chat_request.response_payload_json`

최종 commit이 성공한 뒤에만 SSE `done` 이벤트를 전송한다.
최종 저장이 실패하면 rollback 후 별도 트랜잭션으로 `failed` 상태를 기록한다.

## SSE 이벤트 스키마

모든 SSE 이벤트에는 `request_id`가 포함된다. 클라이언트는 이 값으로 이후 상태 조회 API를 호출할 수 있다.

| 이벤트 type | 추가 필드 |
|---|---|
| `intent` | `intent`, `request_id` |
| `token` | `delta`, `request_id` |
| `progress` | `flow`, `node`, `status`, `step`, `total`, `request_id` |
| `done` | `payload` (ChatMessageSendResponse), `request_id` |
| `error` | `code`, `message`, `request_id` |
| `cancelled` | `status: "cancelled"`, `request_id` |

## 연결 종료와 복구

SSE 연결 종료는 사용자 취소로 보지 않는다.
연결이 끊겨도 서버는 실행 중인 Handler 결과를 저장하려고 시도한다.

`asyncio.CancelledError`가 발생하면 `_run_disconnect_recovery()`를 `asyncio.create_task()`로 백그라운드 실행한다.
복구 태스크는 별도 `AsyncSession`을 열어 AI 그래프를 재실행하고, 결과를 저장한다.

복구 태스크가 실행되는 동안 기존 `chat_request`의 상태는 `processing`으로 유지된다.
클라이언트는 `GET /chat/requests/{request_id}`를 폴링하거나
`GET /chat/sessions/{chat_session_id}/requests/incomplete/latest`로 미완료 요청을 확인할 수 있다.

명시적인 세션 삭제처럼 `chat_cancel_registry`에 cancel 이벤트가 올라온 경우에만 `cancelled` 상태로 처리한다.

## Idempotency

클라이언트는 `idempotency_key`(최대 120자)를 요청 body에 포함할 수 있다.
동일 `chat_session_id`에서 같은 `idempotency_key`가 재전송되면 새 Handler를 실행하지 않는다.

| 기존 상태 | 동작 |
|---|---|
| `completed` | 저장된 `ChatMessageSendResponse` payload를 반환한다 |
| `processing` | 현재 요청 상태만 반환한다 |
| `failed` | 오류와 retry 가능 여부를 반환한다 |
| `cancelled` | 취소 상태를 반환한다 |

`idempotency_key`가 없는 기존 요청과의 호환성은 PostgreSQL UNIQUE가 NULL을 중복 허용하므로 자동으로 보장된다.

## 상태 조회 API

### `GET /chat/requests/{request_id}`

`ChatRequestStatusResponse`를 반환한다. 다른 사용자의 request에 접근하면 404를 반환한다.

### `GET /chat/sessions/{chat_session_id}/requests/incomplete/latest`

해당 세션에서 `status = processing`인 가장 최근 요청을 반환한다.
없으면 `null`을 반환한다.

### ChatRequestStatusResponse 필드

| 필드 | 설명 |
|---|---|
| `request_id` | 요청 ID |
| `chat_session_id` | 세션 ID |
| `user_message_id` | 사용자 메시지 ID |
| `idempotency_key` | 클라이언트가 보낸 idempotency key (없으면 null) |
| `status` | `processing` / `completed` / `failed` / `cancelled` |
| `intent` | 완료 시 감지된 intent |
| `error_code` | 실패 시 오류 코드 |
| `error_message` | 실패 시 오류 메시지 |
| `assistant_message_id` | 완료 시 생성된 assistant 메시지 ID |
| `retryable` | `status = failed`이고 `error_code != INVALID_INPUT`이면 true |
| `payload` | 완료 시 `ChatMessageSendResponse` JSON |
| `created_at` / `completed_at` / `updated_at` | 타임스탬프 |

## 오래된 processing 정리

서버 재시작 또는 작업 중단으로 `processing` 상태가 남을 수 있다.
현재 기준은 `updated_at`이 5분 이상 지난 processing 요청이다.

정리 함수:

```python
await ChatRequestRepository.mark_stale_processing_failed(db)
```

이 함수는 오래된 processing 요청을 다음 상태로 바꾼다.

- `status = failed`
- `error_code = STALE_PROCESSING`
- `error_message = 서버 재시작 또는 작업 중단으로 처리 상태가 만료되었습니다.`

재시도는 클라이언트가 새 `idempotency_key` 또는 기존 실패 상태 확인 후 새 요청으로 수행한다.
