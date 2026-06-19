from typing import Any


class PolicyRagRepository:
    @staticmethod
    async def find_embedding_targets(
        conn,
        limit: int,
        source_type: str | None = None,
    ) -> list[dict[str, Any]]:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    SELECT
                        c.chunk_id,
                        c.document_id,
                        c.chunk_index,
                        c.chunk_text,
                        c.metadata_json,
                        d.source_title,
                        d.source_url,
                        d.source_type,
                        p.policy_id,
                        p.policy_code,
                        p.policy_name
                    FROM policy_document_chunk c
                    JOIN policy_document d ON d.document_id = c.document_id
                    JOIN policy p ON p.policy_id = d.policy_id
                    WHERE c.chunk_text IS NOT NULL
                      AND btrim(c.chunk_text) <> ''
                      AND (%s::varchar IS NULL OR d.source_type = %s::varchar)
                    ORDER BY c.chunk_id
                    LIMIT %s
                """,
                (source_type, source_type, limit),
            )
            rows = await cur.fetchall()

        columns = [
            "chunk_id",
            "document_id",
            "chunk_index",
            "chunk_text",
            "metadata_json",
            "source_title",
            "source_url",
            "source_type",
            "policy_id",
            "policy_code",
            "policy_name",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]
