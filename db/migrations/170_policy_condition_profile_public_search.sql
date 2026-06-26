-- Public policy search now reads policy_condition_profile target/source text.
-- Keep these indexes separate from #148 so existing databases can adopt the
-- policy_condition_profile-centered lookup path without rebuilding the table.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS ix_policy_condition_profile_target_summary_trgm
    ON policy_condition_profile USING gin (target_summary gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ix_policy_condition_profile_source_text_trgm
    ON policy_condition_profile USING gin (source_text gin_trgm_ops);

COMMIT;
