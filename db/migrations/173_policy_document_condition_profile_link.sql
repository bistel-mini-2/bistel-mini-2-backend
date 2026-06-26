-- Issue #173: POLICY_DETAIL RAG 문서와 condition profile 연결
--
-- policy / policy_detail은 원본 저장용으로 유지하고,
-- POLICY_DETAIL RAG 문서는 정제된 policy_condition_profile 기준으로 생성한다.

BEGIN;

ALTER TABLE policy_document
    ADD COLUMN IF NOT EXISTS condition_profile_id BIGINT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.table_constraints
        WHERE table_schema = 'public'
          AND table_name = 'policy_document'
          AND constraint_name = 'policy_document_condition_profile_id_fkey'
    ) THEN
        ALTER TABLE policy_document
            ADD CONSTRAINT policy_document_condition_profile_id_fkey
            FOREIGN KEY (condition_profile_id)
            REFERENCES policy_condition_profile(condition_profile_id)
            ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS policy_document_condition_profile_id_idx
    ON policy_document(condition_profile_id);

COMMIT;
