-- Issue #83: chat_message_policy / chat_message_evidence 테이블 + Supervisor 8노드 정규화
--
-- 적용 범위:
--   1) 기존 chat_message rows 폐기 (structured_json에 박혀 있던 비정규화 데이터)
--   2) chat_message_policy 신규 생성 또는 누락 제약·인덱스 보강
--   3) chat_message_evidence 신규 생성 또는 누락 제약·인덱스 보강
--   4) assessment_evidence.evidence_role enum 일치화 (chat_message_evidence와 동일 5종)
--
-- 결정사항:
--   - chat_session row는 폐기하지 않고 last_message_at/latest_request_id만 NULL 초기화한다.
--     세션 폐기까지 원하면 아래 OPTIONAL 블록을 활성화한다.
--   - evidence_role enum은 DB 대문자(SUMMARY|TARGET|BENEFIT|APPLICATION|CAUTION),
--     API 응답은 소문자(summary|target|benefit|application|caution).
--   - policy_summary intent는 chat_message_policy row를 생성하지 않는다.
--     거론된 정책은 chat_message_evidence.chunk_id 역추적으로 조회한다.
--
-- 적용 메모:
--   - chat_message_policy / chat_message_evidence는 SQLAlchemy autocreate로
--     이미 생성된 상태일 수 있어 IF NOT EXISTS 및 DROP/ADD 패턴으로 idempotent하게 처리한다.

BEGIN;

-- 1) chat_message 폐기
--    parent_message_id 자기참조와 follow_up_question.answer_message_id는
--    ON DELETE SET NULL이므로 DELETE를 사용한다 (TRUNCATE CASCADE는 참조 테이블까지 비움).
DELETE FROM chat_message;
ALTER SEQUENCE chat_message_chat_message_id_seq RESTART WITH 1;

-- 2) chat_session 메타 NULL 초기화 (메시지가 없는 상태와 정합성 유지)
UPDATE chat_session
   SET last_message_at = NULL,
       latest_request_id = NULL;

-- OPTIONAL: 세션 자체를 폐기하려면 아래 두 줄의 주석을 해제한다.
-- DELETE FROM chat_session;
-- ALTER SEQUENCE chat_session_chat_session_id_seq RESTART WITH 1;

-- 3) chat_message_policy 생성·보강
CREATE TABLE IF NOT EXISTS chat_message_policy (
    chat_message_policy_id BIGSERIAL PRIMARY KEY,
    chat_message_id BIGINT NOT NULL
        REFERENCES chat_message(chat_message_id) ON DELETE CASCADE,
    policy_id BIGINT NOT NULL
        REFERENCES policy(policy_id) ON DELETE CASCADE,
    action_type VARCHAR(30) NOT NULL
);

ALTER TABLE chat_message_policy
    DROP CONSTRAINT IF EXISTS chat_message_policy_action_type_check;
ALTER TABLE chat_message_policy
    ADD CONSTRAINT chat_message_policy_action_type_check
        CHECK (action_type IN ('RECOMMENDED', 'COMPARED', 'ELIGIBILITY_TARGET', 'APPLY_TARGET'));

CREATE INDEX IF NOT EXISTS idx_chat_message_policy_message
    ON chat_message_policy(chat_message_id);
CREATE INDEX IF NOT EXISTS idx_chat_message_policy_policy_action
    ON chat_message_policy(policy_id, action_type);

-- 4) chat_message_evidence 생성·보강
CREATE TABLE IF NOT EXISTS chat_message_evidence (
    chat_message_evidence_id BIGSERIAL PRIMARY KEY,
    chat_message_id BIGINT NOT NULL
        REFERENCES chat_message(chat_message_id) ON DELETE CASCADE,
    chunk_id BIGINT NOT NULL
        REFERENCES policy_document_chunk(chunk_id) ON DELETE CASCADE,
    snippet TEXT,
    evidence_role VARCHAR(30)
);

ALTER TABLE chat_message_evidence
    DROP CONSTRAINT IF EXISTS chat_message_evidence_evidence_role_check;
ALTER TABLE chat_message_evidence
    ADD CONSTRAINT chat_message_evidence_evidence_role_check
        CHECK (evidence_role IS NULL OR evidence_role IN ('SUMMARY', 'TARGET', 'BENEFIT', 'APPLICATION', 'CAUTION'));

CREATE INDEX IF NOT EXISTS idx_chat_message_evidence_message
    ON chat_message_evidence(chat_message_id);
CREATE INDEX IF NOT EXISTS idx_chat_message_evidence_chunk
    ON chat_message_evidence(chunk_id);

-- 5) assessment_evidence.evidence_role enum 일치화
--    현재 데이터 0건이라 백필 불필요. chat_message_evidence와 동일 5종으로 통일.
ALTER TABLE assessment_evidence
    DROP CONSTRAINT IF EXISTS assessment_evidence_evidence_role_check;
ALTER TABLE assessment_evidence
    ADD CONSTRAINT assessment_evidence_evidence_role_check
        CHECK (evidence_role IS NULL OR evidence_role IN ('SUMMARY', 'TARGET', 'BENEFIT', 'APPLICATION', 'CAUTION'));

COMMIT;
