from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import DbSessionDep


router = APIRouter(prefix="/health", tags=["health"])

REQUIRED_SCHEMA_COLUMNS = {
    "chat_request": {"idempotency_key", "status", "updated_at"},
    "recommendation_request": {"idempotency_key", "request_status", "updated_at"},
    "eligibility_request": {"idempotency_key", "request_status", "updated_at"},
}


@router.get("/live", include_in_schema=False)
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", include_in_schema=False)
async def ready(db: DbSessionDep) -> JSONResponse:
    checks: dict[str, Any] = {
        "database": {"status": "unknown"},
        "schema": {"status": "unknown", "missing": {}},
    }
    http_status = status.HTTP_200_OK

    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
        missing = await _missing_required_columns(db)
        if missing:
            checks["schema"] = {"status": "missing", "missing": missing}
            http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        else:
            checks["schema"] = {"status": "ok", "missing": {}}
    except Exception as exc:
        checks["database"] = {"status": "error", "error": exc.__class__.__name__}
        checks["schema"] = {"status": "not_checked", "missing": {}}
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ready" if http_status == status.HTTP_200_OK else "not_ready",
            "checks": checks,
        },
    )


async def _missing_required_columns(db: AsyncSession) -> dict[str, list[str]]:
    result = await db.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN :table_names
            """
        ).bindparams(bindparam("table_names", expanding=True)),
        {"table_names": list(REQUIRED_SCHEMA_COLUMNS)},
    )
    found: dict[str, set[str]] = {table: set() for table in REQUIRED_SCHEMA_COLUMNS}
    for table_name, column_name in result.all():
        if table_name in found:
            found[table_name].add(column_name)

    return {
        table: sorted(required_columns - found[table])
        for table, required_columns in REQUIRED_SCHEMA_COLUMNS.items()
        if required_columns - found[table]
    }
