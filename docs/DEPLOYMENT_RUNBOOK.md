# Dodam Backend Deployment Runbook

Status: U10 deployed baseline
Verified at: 2026-08-18

## Target Topology

- Backend: Render web service from `Dockerfile`
- Database: Supabase PostgreSQL
- Frontend: separate Vercel project
- External AI: existing OpenAI API key

Current deployed backend URL:

- `https://dodam-backend.onrender.com`

Render uses the Dockerfile build path. The Render service health check is kept
at `/health/live` so a fresh database can be bootstrapped without blocking the
process. Operational readiness must still be checked with `/health/ready`.

## Required Runtime Variables

Set these as Render service variables. Do not bake them into the image.

- `APP_ENV`
- `APP_NAME`
- `DATABASE_URL`: Supabase PostgreSQL URL using the SQLAlchemy asyncpg driver,
  for example `postgresql+asyncpg://...`
- `PSYCOPG_DATABASE_URL`: Supabase PostgreSQL URL using the psycopg-compatible
  driver, for example `postgresql://...`
- `DATA_GO_KR_SERVICE_KEY`
- `OPENAI_API_KEY`
- `JWT_SECRET_KEY`
- `JWT_ALGORITHM`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `PORT` supplied by Render

## Build

```bash
docker build -t dodam-backend:u9 .
```

The image excludes `.env`, `.git`, local caches, test output, docs, and local SQLite data through `.dockerignore`.

## Local Startup Smoke

Use a test PostgreSQL database with the migrations already applied.

```bash
docker run --rm \
  -p 8000:8000 \
  -e PORT=8000 \
  -e APP_ENV=local-container \
  -e APP_NAME=policy-rag-backend \
  -e DATABASE_URL="$DATABASE_URL" \
  -e PSYCOPG_DATABASE_URL="$PSYCOPG_DATABASE_URL" \
  -e DATA_GO_KR_SERVICE_KEY="${DATA_GO_KR_SERVICE_KEY:-change-me}" \
  -e OPENAI_API_KEY="${OPENAI_API_KEY:-change-me}" \
  -e JWT_SECRET_KEY="$JWT_SECRET_KEY" \
  -e JWT_ALGORITHM=HS256 \
  -e ACCESS_TOKEN_EXPIRE_MINUTES=20160 \
  dodam-backend:u9
```

## Health Contract

- `GET /health/live`: process liveness only; does not check DB or OpenAI.
- `GET /health/ready`: checks DB connectivity and required lifecycle schema columns.
- OpenAI success is not part of liveness or readiness.

Expected readiness failures:

- DB connection failure: HTTP 503, `checks.database.status = "error"`.
- Missing lifecycle schema column: HTTP 503, `checks.schema.status = "missing"`.
- Valid DB and schema: HTTP 200, `status = "ready"`.

Startup DB maintenance uses a bounded timeout so the process can still expose
`/health/live` and `/health/ready` when the database is temporarily unavailable.

## Migration Contract

- Apply Supabase migrations before readiness is expected to pass.
- Startup does not run destructive migrations or seed/demo data.
- Seed/demo data is operationally separate from schema migration.
- Platform rollback does not roll back database migrations; migration rollback requires an explicit database plan.

## Supabase Migration Contract

Supabase CLI migrations are stored under:

```text
supabase/migrations/
```

Apply with:

```bash
npx supabase db push \
  --db-url "$SUPABASE_DB_URL" \
  --password "$SUPABASE_DB_PASSWORD" \
  --include-all
```

The Supabase migration set includes a SQLAlchemy-generated bootstrap schema and
non-purge schema migrations. It intentionally excludes destructive purge files:

- `db/migrations/83b_chat_message_legacy_purge.sql`
- `db/migrations/171_policy_assessment_refresh_purge.sql`

## Render Deployment Order

1. Create or verify the Supabase PostgreSQL project.
2. Configure Render service variables from Supabase connection strings.
3. Apply Supabase migrations with `npx supabase db push`.
4. Deploy or redeploy the Render backend service.
5. Confirm `GET /health/live` returns HTTP 200.
6. Confirm `GET /health/ready` returns HTTP 200 with DB and schema checks ok.
7. Point the Vercel frontend to the backend public URL.

## Verified Deployment Smoke

Checked on 2026-08-18 KST:

- `GET https://dodam-backend.onrender.com/health/live`: HTTP 200, `{"status":"ok"}`
- `GET https://dodam-backend.onrender.com/health/ready`: HTTP 200,
  `database.status = "ok"` and `schema.status = "ok"`

## Boundaries

- This runbook proves backend process, DB connectivity, and required schema
  readiness at the checked timestamp only.
- It does not prove OpenAI generation success, policy data freshness, browser
  E2E behavior, user signup/login success, latency SLA, or continuous uptime.
- Render/Vercel rollback does not roll back Supabase migrations or data.
- Render health checks and `/health/ready` are deployment/readiness checks, not
  continuous monitoring or alerting.
