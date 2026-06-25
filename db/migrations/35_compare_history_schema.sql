-- Issue #35: 비교 이력 저장 테이블 생성
--
-- 적용 범위:
--   1) compare_history: 비교 실행 이력 헤더
--   2) compare_history_item: 비교 이력에 포함된 정책 목록
--
-- 운영 안전성:
--   CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS만 사용하며 기존 데이터를 삭제하지 않는다.

BEGIN;

CREATE TABLE IF NOT EXISTS compare_history (
    compare_history_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL
        REFERENCES users(user_id) ON DELETE CASCADE,
    title VARCHAR(255),
    compared_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS compare_history_item (
    compare_history_item_id BIGSERIAL PRIMARY KEY,
    compare_history_id BIGINT NOT NULL
        REFERENCES compare_history(compare_history_id) ON DELETE CASCADE,
    policy_id BIGINT NOT NULL
        REFERENCES policy(policy_id) ON DELETE CASCADE,
    added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE compare_history
    ADD COLUMN IF NOT EXISTS title VARCHAR(255);
ALTER TABLE compare_history
    ADD COLUMN IF NOT EXISTS compared_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;
ALTER TABLE compare_history
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;
ALTER TABLE compare_history_item
    ADD COLUMN IF NOT EXISTS added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP;

CREATE INDEX IF NOT EXISTS compare_history_user_id_compared_at_idx
    ON compare_history(user_id, compared_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS compare_history_item_compare_history_id_policy_id_idx
    ON compare_history_item(compare_history_id, policy_id);

COMMIT;
