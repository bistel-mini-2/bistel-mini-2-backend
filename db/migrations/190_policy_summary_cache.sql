CREATE TABLE IF NOT EXISTS policy_summary_cache (
    summary_id bigserial PRIMARY KEY,
    policy_id bigint NOT NULL UNIQUE
        REFERENCES policy(policy_id) ON DELETE CASCADE,
    condition_profile_id bigint
        REFERENCES policy_condition_profile(condition_profile_id)
        ON DELETE SET NULL,
    condition_profile_updated_at timestamp,
    summary_source varchar(50) NOT NULL DEFAULT 'policy_condition_profile',
    request_status varchar(50) NOT NULL DEFAULT 'PROCESSING',
    summary text,
    evidence_json jsonb,
    error_message text,
    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS policy_summary_cache_policy_id_idx
ON policy_summary_cache (policy_id);

CREATE INDEX IF NOT EXISTS policy_summary_cache_condition_profile_id_idx
ON policy_summary_cache (condition_profile_id);
