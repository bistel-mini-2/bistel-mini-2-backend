-- Issue #83 (b): chat_message 비정규화 레거시 데이터 폐기 (DEV / STAGING 전용)
--
-- ⚠️  경고
--   - 이 SQL은 chat_message 테이블의 모든 row를 DELETE하고 시퀀스를 reset한다.
--   - 운영 DB에는 적용 금지. 백업 없이 실행하지 말 것.
--   - 적용 전 반드시 다음 두 가지를 수행:
--       1) 환경 확인:  SELECT current_database(), inet_server_addr();
--       2) 백업:       pg_dump "$PSYCOPG_DATABASE_URL" -t chat_message -t chat_session > backup.sql
--
-- 적용 범위:
--   1) chat_message rows 폐기 (structured_json에 박혀 있던 비정규화 policies / evidences)
--   2) chat_session.last_message_at / latest_request_id NULL 초기화
--
-- 폐기 결정 근거 (ADR-005):
--   기존 structured_json.evidences[]에 chunk_id가 저장돼 있지 않아 chat_message_evidence로
--   backfill이 불가능. policies는 부분 backfill 가능하지만 evidence와의 정합성이 깨지므로
--   dev/staging에서는 전면 폐기가 더 안전하다고 판단.
--
--   운영 DB가 도입되어 채팅 이력 보존이 필요해진 시점에는 본 파일을 사용하지 말고,
--   별도 backfill migration을 작성한다 (policies는 _supervisor.intent 기반 action_type 추론,
--   evidences는 폐기 또는 fresh RAG 재검색으로 대체).
--
-- 적용:
--   psql "$PSYCOPG_DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/83b_chat_message_legacy_purge.sql

BEGIN;

-- 1) chat_message 폐기
--    parent_message_id 자기참조와 follow_up_question.answer_message_id는
--    ON DELETE SET NULL이므로 DELETE를 사용한다 (TRUNCATE CASCADE는 참조 테이블까지 비움).
DELETE FROM chat_message;
ALTER SEQUENCE chat_message_chat_message_id_seq RESTART WITH 1;

-- 2) chat_session 메타 NULL 초기화 (메시지가 없는 상태와 정합성 유지)
UPDATE chat_session
   SET last_message_at = NULL,
       latest_request_id = NULL;

-- OPTIONAL: 세션 자체를 폐기하려면 아래 두 줄의 주석을 해제한다.
-- DELETE FROM chat_session;
-- ALTER SEQUENCE chat_session_chat_session_id_seq RESTART WITH 1;

COMMIT;
