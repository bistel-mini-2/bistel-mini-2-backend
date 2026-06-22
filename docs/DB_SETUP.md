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
