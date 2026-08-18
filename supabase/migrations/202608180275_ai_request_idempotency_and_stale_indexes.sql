-- U7: recommendation/eligibility request idempotency and stale-processing cleanup support.
--
-- Adds optional idempotency keys for duplicate-submit protection and status/updated_at
-- indexes for startup cleanup of old PROCESSING requests. NULL keys remain allowed.

BEGIN;

ALTER TABLE recommendation_request
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(120);

ALTER TABLE eligibility_request
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(120);

CREATE UNIQUE INDEX IF NOT EXISTS recommendation_request_user_idempotency_key_uidx
    ON recommendation_request(user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS eligibility_request_user_idempotency_key_uidx
    ON eligibility_request(user_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS recommendation_request_status_updated_at_idx
    ON recommendation_request(request_status, updated_at);

CREATE INDEX IF NOT EXISTS eligibility_request_status_updated_at_idx
    ON eligibility_request(request_status, updated_at);

COMMIT;
