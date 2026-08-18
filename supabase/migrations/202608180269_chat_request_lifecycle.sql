-- Issue #269: 채팅 SSE 요청 상태 저장 및 복구
--
-- 목적:
--   SSE 연결이 끊겨도 request_id로 채팅 요청 상태와 최종 응답 payload를 복구한다.
--
-- 운영 안전성:
--   - CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS 사용.
--   - 동일 세션의 idempotency_key 중복 실행 방지를 위해 UNIQUE(chat_session_id, idempotency_key)를 둔다.
--   - PostgreSQL UNIQUE는 NULL을 중복 허용하므로 idempotency_key 미사용 기존 요청과 호환된다.
--
-- 상태 정리 정책:
--   - 서버 재시작 또는 작업 중단으로 5분 이상 processing 상태가 유지된 요청은 retry 가능한 failed로 정리한다.
--   - 런타임 정리는 ChatRequestRepository.mark_stale_processing_failed()를 수동 또는 시작 작업에서 호출할 수 있다.

BEGIN;

CREATE TABLE IF NOT EXISTS chat_request (
    request_id BIGSERIAL PRIMARY KEY,
    chat_session_id BIGINT NOT NULL
        REFERENCES chat_session(chat_session_id) ON DELETE CASCADE,
    user_message_id BIGINT NOT NULL
        REFERENCES chat_message(chat_message_id) ON DELETE CASCADE,
    idempotency_key VARCHAR(120),
    status VARCHAR(30) NOT NULL DEFAULT 'processing',
    intent VARCHAR(50),
    error_code VARCHAR(80),
    error_message TEXT,
    assistant_message_id BIGINT
        REFERENCES chat_message(chat_message_id) ON DELETE SET NULL,
    response_payload_json JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chat_request_status_check
        CHECK (status IN ('processing', 'completed', 'failed', 'cancelled')),
    CONSTRAINT chat_request_session_idempotency_key_uk
        UNIQUE (chat_session_id, idempotency_key)
);

ALTER TABLE chat_request ADD COLUMN IF NOT EXISTS intent VARCHAR(50);
ALTER TABLE chat_request ADD COLUMN IF NOT EXISTS error_code VARCHAR(80);
ALTER TABLE chat_request ADD COLUMN IF NOT EXISTS error_message TEXT;
ALTER TABLE chat_request ADD COLUMN IF NOT EXISTS assistant_message_id BIGINT;
ALTER TABLE chat_request ADD COLUMN IF NOT EXISTS response_payload_json JSONB;
ALTER TABLE chat_request ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS chat_request_session_status_idx
    ON chat_request(chat_session_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS chat_request_updated_at_idx
    ON chat_request(updated_at);

COMMIT;
