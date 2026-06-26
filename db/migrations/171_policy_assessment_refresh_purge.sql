-- WARNING:
--   이 SQL은 기존 eligibility_request / policy_assessment /
--   assessment_evidence 데이터를 삭제하는 purge migration입니다.
--   dev / staging 전용으로 적용하고, 운영 DB에는 적용하지 않습니다.
--   적용 전 대상 테이블 백업을 먼저 수행하세요.
--
-- Issue #171: policy_condition_profile / policy_rule 최신화 이후
-- 기존 지원 가능성 판정 요청과 결과를 삭제한다.
--
-- 배경:
--   - policy_assessment는 특정 요청 시점의 판정 결과이다.
--   - 정책 조건 기준이 policy_condition_profile 중심으로 정리되면서,
--     기존 eligibility_detail 결과는 과거 조건 기준으로 생성되었을 수 있다.
--
-- 처리:
--   1) eligibility_detail assessment와 연결된 eligibility_request 식별
--   2) assessment_evidence 삭제
--   3) policy_assessment 삭제
--   4) eligibility_request 삭제
--
-- 새 지원 가능성 분석 요청은 최신 policy_condition_profile / policy_rule 기준으로
-- policy_assessment를 다시 생성한다.

DO $$
BEGIN
    IF to_regclass('public.policy_assessment') IS NOT NULL
       AND to_regclass('public.eligibility_request') IS NOT NULL
       AND to_regclass('public.assessment_evidence') IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'policy_assessment'
             AND column_name = 'eligibility_request_id'
       )
    THEN
        CREATE TEMP TABLE IF NOT EXISTS tmp_stale_eligibility_assessment_ids (
            assessment_id bigint PRIMARY KEY
        ) ON COMMIT DROP;

        CREATE TEMP TABLE IF NOT EXISTS tmp_stale_eligibility_request_ids (
            request_id bigint PRIMARY KEY
        ) ON COMMIT DROP;

        TRUNCATE tmp_stale_eligibility_assessment_ids;
        TRUNCATE tmp_stale_eligibility_request_ids;

        INSERT INTO tmp_stale_eligibility_assessment_ids (assessment_id)
        SELECT assessment_id
        FROM policy_assessment
        WHERE assessment_type = 'eligibility_detail'
          AND eligibility_request_id IS NOT NULL
        ON CONFLICT DO NOTHING;

        INSERT INTO tmp_stale_eligibility_request_ids (request_id)
        SELECT DISTINCT eligibility_request_id
        FROM policy_assessment
        WHERE assessment_type = 'eligibility_detail'
          AND eligibility_request_id IS NOT NULL
        ON CONFLICT DO NOTHING;

        DELETE FROM assessment_evidence ae
        USING tmp_stale_eligibility_assessment_ids stale
        WHERE ae.assessment_id = stale.assessment_id;

        DELETE FROM policy_assessment pa
        USING tmp_stale_eligibility_assessment_ids stale
        WHERE pa.assessment_id = stale.assessment_id;

        DELETE FROM eligibility_request er
        USING tmp_stale_eligibility_request_ids stale
        WHERE er.request_id = stale.request_id;
    ELSIF to_regclass('public.policy_assessment') IS NOT NULL
       AND to_regclass('public.eligibility_request') IS NOT NULL
       AND EXISTS (
           SELECT 1
           FROM information_schema.columns
           WHERE table_schema = 'public'
             AND table_name = 'policy_assessment'
             AND column_name = 'eligibility_request_id'
       )
    THEN
        CREATE TEMP TABLE IF NOT EXISTS tmp_stale_eligibility_request_ids (
            request_id bigint PRIMARY KEY
        ) ON COMMIT DROP;

        TRUNCATE tmp_stale_eligibility_request_ids;

        INSERT INTO tmp_stale_eligibility_request_ids (request_id)
        SELECT DISTINCT eligibility_request_id
        FROM policy_assessment
        WHERE assessment_type = 'eligibility_detail'
          AND eligibility_request_id IS NOT NULL
        ON CONFLICT DO NOTHING;

        DELETE FROM policy_assessment
        WHERE assessment_type = 'eligibility_detail'
          AND eligibility_request_id IS NOT NULL;

        DELETE FROM eligibility_request er
        USING tmp_stale_eligibility_request_ids stale
        WHERE er.request_id = stale.request_id;
    END IF;
END $$;
