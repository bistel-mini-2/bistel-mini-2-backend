# 데이터베이스 스키마 적용 가이드

## 마이그레이션 도구

현재 프로젝트는 alembic 등 마이그레이션 도구를 사용하지 않는다. DDL은 수동 SQL 파일로 적용한다. 모델/스키마 변경 시 아래 절차를 따른다.

## 1. DDL 스크립트 위치

`db/migrations/` 디렉토리에 SQL 파일을 둔다. 파일명은 이슈 번호로 시작한다.

```text
db/migrations/
├── 83_chat_message_normalization.sql
└── ...
```

각 파일은 `BEGIN; ... COMMIT;`으로 감싸 트랜잭션 경계를 명시한다.

## 2. 모델·문서 동기화 체크리스트

DDL 변경 시 아래 3개를 같은 PR에서 함께 갱신한다.

- `app/db/models/` 아래 SQLAlchemy 모델 추가·수정
- `app/db/models/__init__.py`의 export 갱신
- `docs/DB_DIAGRAM.md`의 DBML 테이블 정의 갱신

## 3. 적용 방법

로컬 PostgreSQL 적용 예시:

```bash
psql "$PSYCOPG_DATABASE_URL" -f db/migrations/83_chat_message_normalization.sql
```

`PSYCOPG_DATABASE_URL`은 `.env`에 정의된 psql 호환 URL을 사용한다.

## 4. 작성 규칙

- `BEGIN; ... COMMIT;` 트랜잭션 경계 필수
- CHECK enum 갱신과 데이터 백필이 동반되는 경우 같은 파일에 백필 SQL 포함 (`DROP CONSTRAINT` → 데이터 변환 → `ADD CONSTRAINT` 순서)
- 데이터 폐기 SQL은 영향 범위(연관 테이블, FK CASCADE)를 주석으로 명시
- `TRUNCATE ... CASCADE`는 FK 참조 테이블까지 비우므로 의도하지 않은 경우 `DELETE FROM` 사용
- 새 시퀀스가 필요한 경우 `RESTART WITH 1` 또는 `ALTER SEQUENCE ... RESTART WITH 1` 명시

## 5. 적용 이력

DDL 적용 여부는 GitHub PR 본문에 기록한다. 별도 적용 이력 테이블은 두지 않으며, 새 환경 구축 시 `db/migrations/` 디렉토리의 SQL 파일을 파일명 오름차순으로 모두 실행한다.

## 6. 데이터 폐기 동반 SQL의 적용 절차

`*_purge.sql` 또는 `DELETE`/`TRUNCATE`/`DROP TABLE`을 포함하는 SQL은 **dev / staging 전용으로 간주하고 운영 DB에는 적용하지 않는다.** 파일 헤더의 경고 문구를 우선 확인한다.

적용이 정말 필요한 경우 다음 절차를 따른다.

### 1) 환경 확인

```bash
psql "$PSYCOPG_DATABASE_URL" -c "SELECT current_database(), inet_server_addr();"
```

운영 DB host/database 이름이 출력되면 즉시 중단한다.

### 2) 백업

영향을 받는 테이블만 dump:

```bash
pg_dump "$PSYCOPG_DATABASE_URL" -t chat_message -t chat_session > backups/$(date +%Y%m%d_%H%M%S)_pre_purge.sql
```

### 3) 적용

```bash
psql "$PSYCOPG_DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/83b_chat_message_legacy_purge.sql
```

### 4) PR 본문에 적용 환경 / 백업 위치 / 적용 시각 기록

```text
- 적용 환경: dev (kosa165.iptime.org:50001 / postgres)
- 백업 파일: backups/20260622_130000_pre_purge.sql
- 적용 시각: 2026-06-22 10:46
- 영향: chat_message 40 rows 삭제, chat_session 3 rows 메타 NULL
```

## 7. 명명 규칙

| suffix | 의미 | 운영 안전성 |
| --- | --- | --- |
| `*_schema.sql` | 테이블 / 제약 / 인덱스 (idempotent) | 안전 |
| `*_purge.sql` | 데이터 폐기 | **dev/staging 전용** |
| `*_backfill.sql` | 기존 데이터 보존하며 새 컬럼 / 테이블 채움 | 운영 적용 가능 (백업 권장) |

같은 이슈의 SQL이 여러 파일로 나뉘는 경우 `83a_*.sql`, `83b_*.sql` 형태로 접미문자(a/b/c)를 붙여 적용 순서를 명시한다.
