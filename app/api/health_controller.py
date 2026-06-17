from fastapi import APIRouter
from sqlalchemy import text

from app.core.dependencies import DbSessionDep


router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/db")
async def health_db(session: DbSessionDep) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {
        "status": "ok",
        "database": "connected",
    }
