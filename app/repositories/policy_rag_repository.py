import re
from typing import Any


POLICY_RAG_METADATA_VERSION = "2026-06-26.1"


class PolicyRagRepository:
    @staticmethod
    async def search_chunks_by_vector(
        conn,
        embedding: list[float],
        limit: int,
        source_type: str | None = None,
        policy_ids: list[int | str] | None = None,
    ) -> list[dict[str, Any]]:
        if not embedding or limit <= 0:
            return []

        numeric_policy_ids, policy_codes = PolicyRagRepository._policy_keys(
            policy_ids
        )
        embedding_literal = "[" + ",".join(map(str, embedding)) + "]"
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    SELECT
                        c.chunk_id,
                        c.document_id,
                        c.chunk_text,
                        c.metadata_json,
                        d.source_title,
                        d.source_url,
                        d.source_type,
                        p.policy_id,
                        p.policy_code,
                        p.policy_name,
                        embedding.embedding <=> %s::vector AS distance
                    FROM langchain_pg_embedding embedding
                    JOIN langchain_pg_collection collection
                      ON collection.uuid = embedding.collection_id
                     AND collection.name = 'policy_documents'
                    JOIN policy_document_chunk c
                      ON embedding.id = c.chunk_id::text
                    JOIN policy_document d ON d.document_id = c.document_id
                    JOIN policy p ON p.policy_id = d.policy_id
                    WHERE c.chunk_text IS NOT NULL
                      AND btrim(c.chunk_text) <> ''
                      AND (%s::varchar IS NULL OR d.source_type = %s::varchar)
                      AND (
                          (%s::bigint[] IS NULL AND %s::varchar[] IS NULL)
                          OR p.policy_id = ANY(
                              COALESCE(%s::bigint[], ARRAY[]::bigint[])
                          )
                          OR p.policy_code = ANY(
                              COALESCE(%s::varchar[], ARRAY[]::varchar[])
                          )
                      )
                    ORDER BY distance, c.chunk_id
                    LIMIT %s
                """,
                (
                    embedding_literal,
                    source_type,
                    source_type,
                    numeric_policy_ids or None,
                    policy_codes or None,
                    numeric_policy_ids or None,
                    policy_codes or None,
                    limit,
                ),
            )
            rows = await cur.fetchall()

        columns = [
            "chunk_id",
            "document_id",
            "chunk_text",
            "metadata_json",
            "source_title",
            "source_url",
            "source_type",
            "policy_id",
            "policy_code",
            "policy_name",
            "distance",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    @staticmethod
    async def search_chunks_by_keywords(
        conn,
        query: str,
        limit: int,
        source_type: str | None = None,
        policy_ids: list[int | str] | None = None,
    ) -> list[dict[str, Any]]:
        keywords = PolicyRagRepository._search_keywords(query)
        if not keywords or limit <= 0:
            return []

        numeric_policy_ids, policy_codes = PolicyRagRepository._policy_keys(
            policy_ids
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    SELECT
                        c.chunk_id,
                        c.document_id,
                        c.chunk_text,
                        c.metadata_json,
                        d.source_title,
                        d.source_url,
                        d.source_type,
                        p.policy_id,
                        p.policy_code,
                        p.policy_name,
                        (
                            SELECT count(*)::double precision
                            FROM unnest(%s::text[]) AS keyword
                            WHERE concat_ws(
                                ' ',
                                p.policy_name,
                                d.source_title,
                                c.chunk_text
                            ) ILIKE '%%' || keyword || '%%'
                        ) / cardinality(%s::text[]) AS keyword_score
                    FROM policy_document_chunk c
                    JOIN policy_document d ON d.document_id = c.document_id
                    JOIN policy p ON p.policy_id = d.policy_id
                    WHERE c.chunk_text IS NOT NULL
                      AND btrim(c.chunk_text) <> ''
                      AND (%s::varchar IS NULL OR d.source_type = %s::varchar)
                      AND (
                          (%s::bigint[] IS NULL AND %s::varchar[] IS NULL)
                          OR p.policy_id = ANY(
                              COALESCE(%s::bigint[], ARRAY[]::bigint[])
                          )
                          OR p.policy_code = ANY(
                              COALESCE(%s::varchar[], ARRAY[]::varchar[])
                          )
                      )
                      AND EXISTS (
                          SELECT 1
                          FROM unnest(%s::text[]) AS keyword
                          WHERE concat_ws(
                              ' ',
                              p.policy_name,
                              d.source_title,
                              c.chunk_text
                          ) ILIKE '%%' || keyword || '%%'
                      )
                    ORDER BY keyword_score DESC, c.chunk_id
                    LIMIT %s
                """,
                (
                    keywords,
                    keywords,
                    source_type,
                    source_type,
                    numeric_policy_ids or None,
                    policy_codes or None,
                    numeric_policy_ids or None,
                    policy_codes or None,
                    keywords,
                    limit,
                ),
            )
            rows = await cur.fetchall()

        columns = [
            "chunk_id",
            "document_id",
            "chunk_text",
            "metadata_json",
            "source_title",
            "source_url",
            "source_type",
            "policy_id",
            "policy_code",
            "policy_name",
            "keyword_score",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    @staticmethod
    def _search_keywords(query: str) -> list[str]:
        keywords = re.findall(r"[0-9A-Za-z가-힣]{2,}", query)
        return list(dict.fromkeys(keywords))

    @staticmethod
    def _policy_keys(
        policy_ids: list[int | str] | None,
    ) -> tuple[list[int], list[str]]:
        numeric_policy_ids: list[int] = []
        policy_codes: list[str] = []
        for policy_id in policy_ids or []:
            if isinstance(policy_id, int):
                numeric_policy_ids.append(policy_id)
                continue
            value = str(policy_id).strip()
            if not value:
                continue
            if value.isdecimal():
                numeric_policy_ids.append(int(value))
            else:
                policy_codes.append(value)
        return (
            list(dict.fromkeys(numeric_policy_ids)),
            list(dict.fromkeys(policy_codes)),
        )

    @staticmethod
    async def find_embedding_targets(
        conn,
        limit: int,
        source_type: str | None = None,
    ) -> list[dict[str, Any]]:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    SELECT to_regclass('public.langchain_pg_collection') IS NOT NULL
                       AND to_regclass('public.langchain_pg_embedding') IS NOT NULL
                """
            )
            vector_tables_exist = (await cur.fetchone())[0]

            if not vector_tables_exist:
                return await PolicyRagRepository._find_all_embedding_targets(
                    cur=cur,
                    limit=limit,
                    source_type=source_type,
                )

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
                        p.policy_name,
                        p.main_category,
                        p.sub_category,
                        p.provider_name,
                        p.provider_type,
                        p.region_scope,
                        p.region_code,
                        p.benefit_type,
                        p.application_status,
                        p.application_start_date,
                        p.application_end_date,
                        md5(c.chunk_text) AS chunk_hash
                    FROM policy_document_chunk c
                    JOIN policy_document d ON d.document_id = c.document_id
                    JOIN policy p ON p.policy_id = d.policy_id
                    LEFT JOIN langchain_pg_collection collection
                        ON collection.name = 'policy_documents'
                    LEFT JOIN langchain_pg_embedding embedding
                        ON embedding.collection_id = collection.uuid
                       AND embedding.id = c.chunk_id::text
                    WHERE c.chunk_text IS NOT NULL
                      AND btrim(c.chunk_text) <> ''
                      AND (%s::varchar IS NULL OR d.source_type = %s::varchar)
                      AND (
                          embedding.id IS NULL
                          OR embedding.cmetadata->>'chunk_hash' IS DISTINCT FROM md5(c.chunk_text)
                          OR embedding.cmetadata->>'metadata_version' IS DISTINCT FROM %s
                      )
                    ORDER BY c.chunk_id
                    LIMIT %s
                """,
                (source_type, source_type, POLICY_RAG_METADATA_VERSION, limit),
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
            "main_category",
            "sub_category",
            "provider_name",
            "provider_type",
            "region_scope",
            "region_code",
            "benefit_type",
            "application_status",
            "application_start_date",
            "application_end_date",
            "chunk_hash",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    @staticmethod
    async def _find_all_embedding_targets(
        cur,
        limit: int,
        source_type: str | None = None,
    ) -> list[dict[str, Any]]:
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
                    p.policy_name,
                    p.main_category,
                    p.sub_category,
                    p.provider_name,
                    p.provider_type,
                    p.region_scope,
                    p.region_code,
                    p.benefit_type,
                    p.application_status,
                    p.application_start_date,
                    p.application_end_date,
                    md5(c.chunk_text) AS chunk_hash
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
            "main_category",
            "sub_category",
            "provider_name",
            "provider_type",
            "region_scope",
            "region_code",
            "benefit_type",
            "application_status",
            "application_start_date",
            "application_end_date",
            "chunk_hash",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]
