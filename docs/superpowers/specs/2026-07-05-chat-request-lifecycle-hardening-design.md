# Chat Request Lifecycle Hardening Design

## Goal

PR 270의 요청 상태 저장 기능을 PostgreSQL에서 안전하게 동작시키고, 서버 재시작 뒤 남은 `processing` 요청을 실제로 정리하며, 요청 처리 중 반복 DDL을 제거한다.

## Scope

- `chat_request` 시간 값은 기존 `TIMESTAMP WITHOUT TIME ZONE` 스키마에 맞춰 UTC naive datetime으로 저장한다.
- 앱 시작 시 `mark_stale_processing_failed()`를 한 번 실행하고 커밋한다.
- `ChatRequestRepository`의 CRUD 경로에서 `ensure_schema()`를 제거한다. 스키마 생성과 변경은 `db/migrations/269_chat_request_lifecycle.sql`이 담당한다.
- 저장 시간과 stale 정리 경계를 단위 테스트로 검증한다.

## Data and Error Flow

완료·실패·취소 시 애플리케이션은 `datetime.now(timezone.utc).replace(tzinfo=None)` 형태의 UTC naive 값을 `completed_at`에 기록한다. stale cutoff도 같은 방식으로 계산해 PostgreSQL의 timezone 없는 컬럼과 비교한다.

앱 lifespan 시작 과정에서는 기존 검색 인덱스 준비 후 별도 SQLAlchemy 세션을 열어 stale 요청을 정리한다. 성공 시 커밋하고, 실패 시 애플리케이션 시작을 중단해 운영자가 마이그레이션 누락이나 DB 문제를 즉시 확인할 수 있게 한다.

## Migration Boundary

런타임 repository는 테이블이 이미 배포되었다고 가정한다. 마이그레이션이 적용되지 않은 환경에서는 요청이 명확하게 실패해야 하며, API 호출 중 `CREATE TABLE`, `ALTER TABLE`, `CREATE INDEX`로 이를 숨기지 않는다.

## Verification

- `mark_completed`, `mark_failed`, `mark_cancelled`가 timezone 정보 없는 UTC datetime을 기록하는지 확인한다.
- stale cutoff 파라미터가 timezone 정보 없는 UTC datetime인지 확인한다.
- repository 조회 및 생성이 `ensure_schema()`를 호출하지 않는지 확인한다.
- 앱 lifespan에서 stale 정리와 commit이 실행되는지 확인한다.
- 채팅 서비스 및 컨트롤러 집중 테스트를 실행한다.

## Non-goals

- SSE 이벤트 계약 변경
- idempotency 동작 변경
- 주기적인 stale 정리 스케줄러 추가
- 기존 마이그레이션 체계 전면 개편
