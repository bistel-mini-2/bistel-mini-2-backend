-- Issue #148: 정책 조건 해석 profile 및 policy_rule 검색/판정 컬럼 보강
--
-- 적용 범위:
--   1) policy_condition_profile 신규 생성
--   2) policy_rule에 condition_json 파생 rule 평가용 컬럼 추가
--
-- 설계 의도:
--   - policy / policy_detail 원본 데이터는 그대로 보존한다.
--   - LLM이 해석한 정책 조건 전체는 policy_condition_profile.condition_json에 저장한다.
--   - 추천/지원 가능성 분석에서 빠르게 평가할 rule row는 policy_rule에 저장한다.
--   - 기존 policy_rule 데이터는 삭제하지 않고 기본 그룹(ALL / AND)으로 해석한다.

BEGIN;

CREATE TABLE IF NOT EXISTS policy_condition_profile (
    condition_profile_id BIGSERIAL PRIMARY KEY,
    policy_id BIGINT NOT NULL
        REFERENCES policy(policy_id) ON DELETE CASCADE,
    condition_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    target_summary TEXT,
    confidence NUMERIC(5, 4),
    review_required BOOLEAN NOT NULL DEFAULT FALSE,
    quality_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_text TEXT,
    source_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    extracted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS policy_condition_profile_policy_uidx
    ON policy_condition_profile(policy_id);
CREATE INDEX IF NOT EXISTS policy_condition_profile_review_idx
    ON policy_condition_profile(review_required);
CREATE INDEX IF NOT EXISTS policy_condition_profile_condition_gin_idx
    ON policy_condition_profile USING GIN(condition_json);

ALTER TABLE policy_rule
    ADD COLUMN IF NOT EXISTS rule_group VARCHAR(100) NOT NULL DEFAULT 'ALL';
ALTER TABLE policy_rule
    ADD COLUMN IF NOT EXISTS group_operator VARCHAR(10) NOT NULL DEFAULT 'AND';
ALTER TABLE policy_rule
    ADD COLUMN IF NOT EXISTS source_text TEXT;
ALTER TABLE policy_rule
    ADD COLUMN IF NOT EXISTS confidence NUMERIC(5, 4);
ALTER TABLE policy_rule
    ADD COLUMN IF NOT EXISTS review_required BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE policy_rule
    ADD COLUMN IF NOT EXISTS is_exclusion BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE policy_rule
    DROP CONSTRAINT IF EXISTS policy_rule_group_operator_check;
ALTER TABLE policy_rule
    ADD CONSTRAINT policy_rule_group_operator_check
        CHECK (group_operator IN ('AND', 'OR'));

CREATE INDEX IF NOT EXISTS policy_rule_policy_group_idx
    ON policy_rule(policy_id, rule_group, group_operator);
CREATE INDEX IF NOT EXISTS policy_rule_review_idx
    ON policy_rule(review_required);
CREATE INDEX IF NOT EXISTS policy_rule_exclusion_idx
    ON policy_rule(is_exclusion);

COMMIT;
