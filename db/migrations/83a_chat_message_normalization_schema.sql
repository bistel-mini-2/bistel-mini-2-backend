-- Issue #83 (a): chat_message 정규화 스키마 적용 (운영 안전)
--
-- 적용 범위:
--   1) chat_message_policy 신규 생성 또는 누락 제약·인덱스 보강
--   2) chat_message_evidence 신규 생성 또는 누락 제약·인덱스 보강
--   3) assessment_evidence.evidence_role enum 일치화 (chat_message_evidence와 동일 5종)
--
-- 운영 안전성:
--   - CREATE TABLE IF NOT EXISTS / DROP CONSTRAINT IF EXISTS / ADD CONSTRAINT 패턴으로 idempotent
--   - 기존 데이터 폐기 동작은 본 파일에 없음. 운영/staging 환경에도 적용 가능
--   - 주의: assessment_evidence에 데이터가 있고 evidence_role이 새 enum 5종
--           (SUMMARY|TARGET|BENEFIT|APPLICATION|CAUTION) 밖이면 CHECK 위반.
--           본 PR 시점 dev DB는 0건이라 안전. 운영 적용 전 다음 쿼리로 점검:
--             SELECT evidence_role, COUNT(*) FROM assessment_evidence GROUP BY evidence_role;
--
-- 결정사항:
--   - evidence_role enum은 DB 대문자(SUMMARY|TARGET|BENEFIT|APPLICATION|CAUTION),
--     API 응답은 소문자(summary|target|benefit|application|caution).
--   - chat_message_evidence/policy 모두 chat_message에 ON DELETE CASCADE.
--   - chat_message_policy / chat_message_evidence는 SQLAlchemy autocreate로
--     이미 생성된 상태일 수 있어 IF NOT EXISTS로 처리.
--
-- 후속:
--   - 기존 chat_message 데이터 폐기가 필요하면 별도 파일 `83b_chat_message_legacy_purge.sql`을 사용.
--     해당 파일은 데이터 폐기 동반이므로 dev/staging 전용.

BEGIN;

-- 1) chat_message_policy 생성·보강
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

-- 2) chat_message_evidence 생성·보강
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

-- 3) assessment_evidence.evidence_role enum 일치화
--    데이터 존재 여부와 무관하게 CHECK는 새 enum으로 통일.
--    운영 적용 전 위 주의사항대로 기존 데이터 점검 필요.
ALTER TABLE assessment_evidence
    DROP CONSTRAINT IF EXISTS assessment_evidence_evidence_role_check;
ALTER TABLE assessment_evidence
    ADD CONSTRAINT assessment_evidence_evidence_role_check
        CHECK (evidence_role IS NULL OR evidence_role IN ('SUMMARY', 'TARGET', 'BENEFIT', 'APPLICATION', 'CAUTION'));

COMMIT;
