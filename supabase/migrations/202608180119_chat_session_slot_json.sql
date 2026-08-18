-- Issue #119: chat_session에 slot_json JSONB 컬럼 추가
--
-- 목적:
--   직전 거론 정책 슬롯을 세션 단위로 보관해서 supervisor의 지시어 해소와
--   Apply/Eligibility branch의 RAG 스킵을 가능하게 한다.
--
-- 스키마:
--   slot_json JSONB NOT NULL DEFAULT '{}'::jsonb
--   구조 예시: {
--     "recent_policies": [
--       {"policy_id": 42, "slug": "child-care", "policy_name": "...", "last_action": "APPLY_TARGET"}
--     ],
--     "updated_at": "2026-06-23T14:00:00Z"
--   }
--
-- 운영 안전성:
--   - ADD COLUMN IF NOT EXISTS + DEFAULT '{}' 로 idempotent. 기존 세션도 빈 dict.

BEGIN;

ALTER TABLE chat_session
    ADD COLUMN IF NOT EXISTS slot_json JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMIT;
