import json
from typing import Any

from langchain_core.documents import Document


class PolicyDocumentRepository:
    @staticmethod
    async def find_policy_detail_sources(conn, limit: int) -> list[dict[str, Any]]:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    SELECT
                        p.policy_id,
                        p.policy_code,
                        p.policy_name,
                        p.main_category,
                        p.sub_category,
                        p.provider_name,
                        p.benefit_type,
                        p.official_url,
                        d.easy_summary,
                        d.target_description,
                        d.benefit_description,
                        d.application_method,
                        d.application_period_text,
                        d.caution
                    FROM policy p
                    JOIN policy_detail d ON d.policy_id = p.policy_id
                    WHERE p.is_active = TRUE
                    ORDER BY p.policy_id
                    LIMIT %s
                """,
                (limit,),
            )
            rows = await cur.fetchall()

        columns = [
            "policy_id",
            "policy_code",
            "policy_name",
            "main_category",
            "sub_category",
            "provider_name",
            "benefit_type",
            "official_url",
            "easy_summary",
            "target_description",
            "benefit_description",
            "application_method",
            "application_period_text",
            "caution",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    @staticmethod
    async def upsert_policy_detail_document(
        conn,
        policy_id: int,
        source_title: str,
        source_url: str | None,
        raw_text: str,
    ) -> int:
        document_id = policy_id * 10000 + 9000
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    INSERT INTO policy_document (
                        document_id,
                        policy_id,
                        source_title,
                        source_url,
                        source_type,
                        raw_text,
                        updated_at
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        'POLICY_DETAIL',
                        %s,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (document_id) DO UPDATE SET
                        policy_id = EXCLUDED.policy_id,
                        source_title = EXCLUDED.source_title,
                        source_url = EXCLUDED.source_url,
                        source_type = EXCLUDED.source_type,
                        raw_text = EXCLUDED.raw_text,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING document_id
                """,
                (document_id, policy_id, source_title, source_url, raw_text),
            )
            row = await cur.fetchone()
        return int(row[0])

    @staticmethod
    async def replace_document_chunks(
        conn,
        document_id: int,
        chunk_documents: list[Document],
    ) -> int:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM policy_document_chunk WHERE document_id = %s",
                (document_id,),
            )

            for chunk_index, document in enumerate(chunk_documents):
                chunk_id = document_id * 10000 + chunk_index
                metadata = dict(document.metadata)
                metadata["chunk_id"] = chunk_id
                metadata["document_id"] = document_id
                metadata["chunk_index"] = chunk_index

                await cur.execute(
                    """
                        INSERT INTO policy_document_chunk (
                            chunk_id,
                            document_id,
                            chunk_index,
                            chunk_text,
                            metadata_json
                        )
                        VALUES (%s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        chunk_id,
                        document_id,
                        chunk_index,
                        document.page_content,
                        json.dumps(metadata, ensure_ascii=False),
                    ),
                )

        return len(chunk_documents)
