-- Issue #28: 관심 정책 저장 테이블 생성 (운영 안전)
--
-- 적용 범위:
--   user_favorites(user_id, policy_id, saved_at)
--
-- 운영 안전성:
--   CREATE TABLE IF NOT EXISTS만 사용하며 기존 데이터를 변경하지 않는다.

BEGIN;

CREATE TABLE IF NOT EXISTS user_favorites (
    user_id BIGINT NOT NULL
        REFERENCES users(user_id) ON DELETE CASCADE,
    policy_id BIGINT NOT NULL
        REFERENCES policy(policy_id) ON DELETE CASCADE,
    saved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, policy_id)
);

COMMIT;
