# Dodam Backend Deployment Runbook

Status: U9 baseline
Verified at: 2026-08-18

## Target Topology

- Backend: Railway web service from `Dockerfile`
- Database: Railway PostgreSQL
- Frontend: separate Vercel project
- External AI: existing OpenAI API key

Railway `railway.json` uses the Dockerfile builder and `/health/ready` as the deployment healthcheck path. Railway healthchecks gate a new deployment before switching traffic; they do not replace continuous monitoring after activation.

## Required Runtime Variables

Set these as Railway service variables. Do not bake them into the image.

- `APP_ENV`
- `APP_NAME`
- `DATABASE_URL`
- `PSYCOPG_DATABASE_URL`
- `DATA_GO_KR_SERVICE_KEY`
- `OPENAI_API_KEY`
- `JWT_SECRET_KEY`
- `JWT_ALGORITHM`
- `ACCESS_TOKEN_EXPIRE_MINUTES`
- `PORT` supplied by Railway

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

- Apply migrations before deploying or before readiness is expected to pass.
- Startup does not run destructive migrations or seed/demo data.
- Seed/demo data is operationally separate from schema migration.
- Platform rollback does not roll back database migrations; migration rollback requires an explicit database plan.

## Railway Deployment Order

1. Provision Railway PostgreSQL.
2. Apply backend migrations to the target database.
3. Configure Railway service variables.
4. Deploy the backend service.
5. Confirm `/health/ready` returns HTTP 200.
6. Point the Vercel frontend to the backend public URL.

## Boundaries

- This runbook is a deployment package baseline, not proof of public production operation.
- Railway healthcheck is a deployment gate, not uptime monitoring.
- U10 must still verify live deployment, smoke scenarios, rollback behavior, cold start, and cost.
