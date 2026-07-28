-- UPSERT on (eligibility_request_id, policy_id, assessment_type) requires a unique partial index.
-- Previously created inline via ensure_policy_assessment_schema(); now owned by migration.
CREATE UNIQUE INDEX IF NOT EXISTS policy_assessment_eligibility_policy_type_uidx
    ON policy_assessment (eligibility_request_id, policy_id, assessment_type)
    WHERE eligibility_request_id IS NOT NULL;
