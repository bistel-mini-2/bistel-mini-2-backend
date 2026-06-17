import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class PolicyRawImportRepository:
    @staticmethod
    async def create_table(db: AsyncSession) -> None:
        await db.execute(
            text("""
                CREATE TABLE IF NOT EXISTS policy_raw_import (
                    import_id BIGSERIAL PRIMARY KEY,
                    serv_id VARCHAR(100) NOT NULL UNIQUE,
                    source_type VARCHAR(50) NOT NULL DEFAULT 'CENTRAL',
                    list_json JSONB,
                    detail_json JSONB,
                    detail_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
                    detail_fetched_at TIMESTAMP,
                    error_message TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """),
        )

    @staticmethod
    async def upsert_list_item(
        db: AsyncSession,
        serv_id: str,
        list_json: dict[str, Any],
    ) -> None:
        await db.execute(
            text("""
                INSERT INTO policy_raw_import (
                    serv_id, source_type, list_json, detail_status
                )
                VALUES (
                    :serv_id, 'CENTRAL', CAST(:list_json AS jsonb), 'PENDING'
                )
                ON CONFLICT (serv_id) DO UPDATE SET
                    list_json = EXCLUDED.list_json,
                    updated_at = CURRENT_TIMESTAMP
            """),
            {
                "serv_id": serv_id,
                "list_json": json.dumps(list_json, ensure_ascii=False),
            },
        )

    @staticmethod
    async def upsert_detail_item(
        db: AsyncSession,
        serv_id: str,
        detail_json: dict[str, Any],
    ) -> None:
        await db.execute(
            text("""
                INSERT INTO policy_raw_import (
                    serv_id, source_type, detail_json, detail_status,
                    detail_fetched_at
                )
                VALUES (
                    :serv_id, 'CENTRAL', CAST(:detail_json AS jsonb),
                    'COMPLETED', CURRENT_TIMESTAMP
                )
                ON CONFLICT (serv_id) DO UPDATE SET
                    detail_json = EXCLUDED.detail_json,
                    detail_status = 'COMPLETED',
                    detail_fetched_at = CURRENT_TIMESTAMP,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
            """),
            {
                "serv_id": serv_id,
                "detail_json": json.dumps(detail_json, ensure_ascii=False),
            },
        )

    @staticmethod
    async def find_pending_serv_ids(
        db: AsyncSession,
        limit: int = 10,
    ) -> list[str]:
        result = await db.execute(
            text("""
                SELECT serv_id
                FROM policy_raw_import
                WHERE detail_status = 'PENDING'
                ORDER BY updated_at ASC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        return list(result.scalars().all())

    @staticmethod
    async def mark_detail_failed(
        db: AsyncSession,
        serv_id: str,
        error_message: str,
    ) -> None:
        await db.execute(
            text("""
                UPDATE policy_raw_import
                SET detail_status = 'FAILED',
                    error_message = :error_message,
                    updated_at = CURRENT_TIMESTAMP
                WHERE serv_id = :serv_id
            """),
            {
                "serv_id": serv_id,
                "error_message": error_message[:2000],
            },
        )
