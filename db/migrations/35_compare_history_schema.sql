-- Issue #35: 비교 이력 저장 테이블 생성
--
-- 적용 범위:
--   compare_history(user_id, policy_a_id, policy_b_id, compared_at)
--
-- 운영 안전성:
--   CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS만 사용하며 기존 데이터를 삭제하지 않는다.

BEGIN;

CREATE TABLE IF NOT EXISTS compare_history (
    compare_history_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL
        REFERENCES users(user_id) ON DELETE CASCADE,
    policy_a_id BIGINT
        REFERENCES policy(policy_id) ON DELETE SET NULL,
    policy_b_id BIGINT
        REFERENCES policy(policy_id) ON DELETE SET NULL,
    title VARCHAR(255),
    compared_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

ALTER TABLE compare_history
    ADD COLUMN IF NOT EXISTS policy_a_id BIGINT;
ALTER TABLE compare_history
    ADD COLUMN IF NOT EXISTS policy_b_id BIGINT;
ALTER TABLE compare_history
    ADD COLUMN IF NOT EXISTS title VARCHAR(255);
ALTER TABLE compare_history
    ADD COLUMN IF NOT EXISTS compared_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE compare_history
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS compare_history_user_compared_idx
    ON compare_history(user_id, compared_at DESC);

COMMIT;
