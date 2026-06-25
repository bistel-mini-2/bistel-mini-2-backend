-- #154: condition_profile 파생 rule과 기존 OpenAPI SQL 추출 rule을 구분하기 위한 origin 컬럼.
--   - 'openapi'         : policy_raw_import 기반 SQL 추출 rule (기존)
--   - 'condition_profile': policy_condition_profile.condition_json 파생 rule (#154)
-- 추천/지원판정은 policy_id별 전체 rule을 읽으므로, profile 파생 rule이 들어간 정책은
-- profile 기준으로 교체하여 이중 적용을 막는다. (충돌 정리는 후속 작업에서 진행)

ALTER TABLE policy_rule
    ADD COLUMN IF NOT EXISTS origin varchar(30) NOT NULL DEFAULT 'openapi';

CREATE INDEX IF NOT EXISTS policy_rule_policy_origin_idx
    ON policy_rule (policy_id, origin);
