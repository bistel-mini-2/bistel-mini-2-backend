class PolicyImportRepository:
    @staticmethod
    async def _fetch_count(conn, query: str) -> int:
        async with conn.cursor() as cur:
            await cur.execute(query)
            row = await cur.fetchone()
            return int(row[0]) if row is not None else 0

    @staticmethod
    async def _execute(conn, query: str) -> None:
        async with conn.cursor() as cur:
            await cur.execute(query)

    @classmethod
    async def upsert_policies(cls, conn) -> int:
        return await cls._fetch_count(
            conn,
            """
                WITH raw AS (
                    SELECT
                        import_id,
                        serv_id,
                        list_json,
                        detail_json,
                        detail_fetched_at
                    FROM policy_raw_import
                    WHERE list_json IS NOT NULL
                      AND detail_json IS NOT NULL
                      AND detail_status = 'COMPLETED'
                ),
                upserted AS (
                    INSERT INTO policy (
                        policy_id,
                        policy_code,
                        policy_name,
                        main_category,
                        sub_category,
                        provider_name,
                        provider_type,
                        region_scope,
                        region_code,
                        benefit_type,
                        application_status,
                        official_url,
                        contact,
                        last_verified_at,
                        is_active,
                        updated_at
                    )
                    SELECT
                        import_id,
                        serv_id,
                        COALESCE(
                            NULLIF(detail_json->>'servNm', ''),
                            NULLIF(list_json->>'servNm', ''),
                            serv_id
                        ),
                        NULLIF(
                            split_part(
                                COALESCE(
                                    NULLIF(detail_json->>'intrsThemaArray', ''),
                                    NULLIF(list_json->>'intrsThemaArray', '')
                                ),
                                ',',
                                1
                            ),
                            ''
                        ),
                        NULLIF(
                            split_part(
                                COALESCE(
                                    NULLIF(detail_json->>'lifeArray', ''),
                                    NULLIF(list_json->>'lifeArray', '')
                                ),
                                ',',
                                1
                            ),
                            ''
                        ),
                        COALESCE(
                            NULLIF(detail_json->>'jurMnofNm', ''),
                            NULLIF(list_json->>'jurMnofNm', '')
                        ),
                        NULLIF(list_json->>'jurOrgNm', ''),
                        'NATIONAL',
                        NULL,
                        COALESCE(
                            NULLIF(detail_json->>'srvPvsnNm', ''),
                            NULLIF(list_json->>'srvPvsnNm', ''),
                            NULLIF(list_json->>'sprtCycNm', '')
                        ),
                        CASE NULLIF(list_json->>'onapPsbltYn', '')
                            WHEN 'Y' THEN 'ONLINE_AVAILABLE'
                            WHEN 'N' THEN 'OFFLINE_ONLY'
                            ELSE NULL
                        END,
                        COALESCE(
                            NULLIF(list_json->>'servDtlLink', ''),
                            (
                                SELECT NULLIF(link_item->>'servSeDetailLink', '')
                                FROM jsonb_array_elements(
                                    COALESCE(detail_json->'inqplHmpgReldList', '[]'::jsonb)
                                ) AS link_item
                                WHERE NULLIF(link_item->>'servSeDetailLink', '') IS NOT NULL
                                LIMIT 1
                            )
                        ),
                        COALESCE(
                            NULLIF(detail_json->>'rprsCtadr', ''),
                            NULLIF(list_json->>'rprsCtadr', ''),
                            (
                                SELECT string_agg(
                                    concat_ws(': ',
                                        NULLIF(contact_item->>'servSeDetailNm', ''),
                                        NULLIF(contact_item->>'servSeDetailLink', '')
                                    ),
                                    E'\n'
                                    ORDER BY contact_item->>'servSeDetailNm'
                                )
                                FROM jsonb_array_elements(
                                    COALESCE(detail_json->'inqplCtadrList', '[]'::jsonb)
                                ) AS contact_item
                            )
                        ),
                        detail_fetched_at,
                        TRUE,
                        CURRENT_TIMESTAMP
                    FROM raw
                    ON CONFLICT (policy_code) DO UPDATE SET
                        policy_name = EXCLUDED.policy_name,
                        main_category = EXCLUDED.main_category,
                        sub_category = EXCLUDED.sub_category,
                        provider_name = EXCLUDED.provider_name,
                        provider_type = EXCLUDED.provider_type,
                        region_scope = EXCLUDED.region_scope,
                        region_code = EXCLUDED.region_code,
                        benefit_type = EXCLUDED.benefit_type,
                        application_status = EXCLUDED.application_status,
                        official_url = EXCLUDED.official_url,
                        contact = EXCLUDED.contact,
                        last_verified_at = EXCLUDED.last_verified_at,
                        is_active = EXCLUDED.is_active,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING policy_id
                )
                SELECT COUNT(*) FROM upserted
            """,
        )

    @classmethod
    async def upsert_policy_details(cls, conn) -> int:
        return await cls._fetch_count(
            conn,
            """
                WITH raw AS (
                    SELECT
                        p.policy_id,
                        r.list_json,
                        r.detail_json
                    FROM policy_raw_import r
                    JOIN policy p ON p.policy_code = r.serv_id
                    WHERE r.list_json IS NOT NULL
                      AND r.detail_json IS NOT NULL
                      AND r.detail_status = 'COMPLETED'
                ),
                upserted AS (
                    INSERT INTO policy_detail (
                        policy_id,
                        easy_summary,
                        target_description,
                        benefit_description,
                        application_method,
                        application_period_text,
                        caution
                    )
                    SELECT
                        policy_id,
                        COALESCE(
                            NULLIF(detail_json->>'wlfareInfoOutlCn', ''),
                            NULLIF(list_json->>'servDgst', '')
                        ),
                        NULLIF(detail_json->>'tgtrDtlCn', ''),
                        NULLIF(detail_json->>'alwServCn', ''),
                        (
                            SELECT string_agg(
                                concat_ws(': ',
                                    NULLIF(method_item->>'servSeDetailNm', ''),
                                    NULLIF(method_item->>'servSeDetailLink', '')
                                ),
                                E'\n'
                                ORDER BY method_item->>'servSeDetailNm',
                                         method_item->>'servSeDetailLink'
                            )
                            FROM jsonb_array_elements(
                                COALESCE(detail_json->'applmetList', '[]'::jsonb)
                            ) AS method_item
                        ),
                        NULL,
                        NULLIF(detail_json->>'slctCritCn', '')
                    FROM raw
                    ON CONFLICT (policy_id) DO UPDATE SET
                        easy_summary = EXCLUDED.easy_summary,
                        target_description = EXCLUDED.target_description,
                        benefit_description = EXCLUDED.benefit_description,
                        application_method = EXCLUDED.application_method,
                        application_period_text = EXCLUDED.application_period_text,
                        caution = EXCLUDED.caution
                    RETURNING policy_id
                )
                SELECT COUNT(*) FROM upserted
            """,
        )

    @classmethod
    async def replace_required_documents(cls, conn) -> int:
        await cls._execute(
            conn,
            """
                DELETE FROM required_document d
                USING policy_raw_import r
                JOIN policy p ON p.policy_code = r.serv_id
                WHERE d.policy_id = p.policy_id
                  AND r.list_json IS NOT NULL
                  AND r.detail_json IS NOT NULL
                  AND r.detail_status = 'COMPLETED'
            """,
        )

        return await cls._fetch_count(
            conn,
            """
                WITH document_items AS (
                    SELECT
                        p.policy_id,
                        form_item.ordinality::int AS item_order,
                        NULLIF(form_item.item->>'servSeDetailNm', '')
                            AS document_name,
                        NULLIF(form_item.item->>'servSeDetailLink', '')
                            AS document_link
                    FROM policy_raw_import r
                    JOIN policy p ON p.policy_code = r.serv_id
                    CROSS JOIN LATERAL jsonb_array_elements(
                        COALESCE(r.detail_json->'basfrmList', '[]'::jsonb)
                    ) WITH ORDINALITY AS form_item(item, ordinality)
                    WHERE r.list_json IS NOT NULL
                      AND r.detail_json IS NOT NULL
                      AND r.detail_status = 'COMPLETED'
                ),
                normalized_documents AS (
                    SELECT
                        (policy_id * 10000 + item_order)::bigint
                            AS required_document_id,
                        policy_id,
                        document_name,
                        'REQUIRED' AS required_type,
                        NULL::text AS issue_place,
                        document_link AS description
                    FROM document_items
                    WHERE document_name IS NOT NULL
                      AND (
                        document_name LIKE '%신청%서%'
                        OR document_name LIKE '%동의서%'
                        OR document_name LIKE '%확인서%'
                        OR document_name LIKE '%위임장%'
                        OR document_name LIKE '%진단서%'
                        OR document_name LIKE '%증명서%'
                      )
                ),
                inserted AS (
                    INSERT INTO required_document (
                        required_document_id,
                        policy_id,
                        document_name,
                        required_type,
                        issue_place,
                        description
                    )
                    SELECT
                        required_document_id,
                        policy_id,
                        document_name,
                        required_type,
                        issue_place,
                        description
                    FROM normalized_documents
                    RETURNING required_document_id
                )
                SELECT COUNT(*) FROM inserted
            """,
        )

    @classmethod
    async def replace_policy_documents(cls, conn) -> int:
        await cls._execute(
            conn,
            """
                DELETE FROM policy_document d
                USING policy_raw_import r
                JOIN policy p ON p.policy_code = r.serv_id
                WHERE d.policy_id = p.policy_id
                  AND r.list_json IS NOT NULL
                  AND r.detail_json IS NOT NULL
                  AND r.detail_status = 'COMPLETED'
            """,
        )

        return await cls._fetch_count(
            conn,
            """
                WITH document_items AS (
                    SELECT
                        p.policy_id,
                        form_item.ordinality::int AS item_order,
                        NULLIF(form_item.item->>'servSeDetailNm', '')
                            AS source_title,
                        NULLIF(form_item.item->>'servSeDetailLink', '')
                            AS source_url
                    FROM policy_raw_import r
                    JOIN policy p ON p.policy_code = r.serv_id
                    CROSS JOIN LATERAL jsonb_array_elements(
                        COALESCE(r.detail_json->'basfrmList', '[]'::jsonb)
                    ) WITH ORDINALITY AS form_item(item, ordinality)
                    WHERE r.list_json IS NOT NULL
                      AND r.detail_json IS NOT NULL
                      AND r.detail_status = 'COMPLETED'
                ),
                normalized_documents AS (
                    SELECT
                        (policy_id * 10000 + 3000 + item_order)::bigint
                            AS document_id,
                        policy_id,
                        source_title,
                        source_url,
                        'POLICY_REFERENCE' AS source_type,
                        NULL::text AS raw_text
                    FROM document_items
                    WHERE source_title IS NOT NULL
                      AND NOT (
                        source_title LIKE '%신청%서%'
                        OR source_title LIKE '%동의서%'
                        OR source_title LIKE '%확인서%'
                        OR source_title LIKE '%위임장%'
                        OR source_title LIKE '%진단서%'
                        OR source_title LIKE '%증명서%'
                      )
                ),
                inserted AS (
                    INSERT INTO policy_document (
                        document_id,
                        policy_id,
                        source_title,
                        source_url,
                        source_type,
                        raw_text,
                        updated_at
                    )
                    SELECT
                        document_id,
                        policy_id,
                        source_title,
                        source_url,
                        source_type,
                        raw_text,
                        CURRENT_TIMESTAMP
                    FROM normalized_documents
                    RETURNING document_id
                )
                SELECT COUNT(*) FROM inserted
            """,
        )

    @classmethod
    async def replace_policy_tags(cls, conn) -> int:
        await cls._execute(
            conn,
            """
                DELETE FROM policy_tag t
                USING policy_raw_import r
                JOIN policy p ON p.policy_code = r.serv_id
                WHERE t.policy_id = p.policy_id
                  AND r.list_json IS NOT NULL
                  AND r.detail_json IS NOT NULL
                  AND r.detail_status = 'COMPLETED'
            """,
        )

        return await cls._fetch_count(
            conn,
            """
                WITH raw_tags AS (
                    SELECT
                        p.policy_id,
                        regexp_split_to_table(
                            concat_ws(
                                ',',
                                NULLIF(COALESCE(r.detail_json->>'lifeArray', r.list_json->>'lifeArray'), ''),
                                NULLIF(COALESCE(
                                    r.detail_json->>'trgterIndvdlArray',
                                    r.list_json->>'trgterIndvdlArray'
                                ), ''),
                                NULLIF(COALESCE(
                                    r.detail_json->>'intrsThemaArray',
                                    r.list_json->>'intrsThemaArray'
                                ), '')
                            ),
                            ','
                        ) AS tag_name
                    FROM policy_raw_import r
                    JOIN policy p ON p.policy_code = r.serv_id
                    WHERE r.list_json IS NOT NULL
                      AND r.detail_json IS NOT NULL
                      AND r.detail_status = 'COMPLETED'
                ),
                normalized_tags AS (
                    SELECT DISTINCT
                        policy_id,
                        trim(tag_name) AS tag_name
                    FROM raw_tags
                    WHERE trim(tag_name) <> ''
                ),
                inserted AS (
                    INSERT INTO policy_tag (policy_id, tag_name)
                    SELECT policy_id, tag_name
                    FROM normalized_tags
                    ON CONFLICT (policy_id, tag_name) DO NOTHING
                    RETURNING policy_id
                )
                SELECT COUNT(*) FROM inserted
            """,
        )

    @classmethod
    async def replace_policy_checklist_templates(cls, conn) -> int:
        await cls._execute(
            conn,
            """
                DELETE FROM policy_checklist_template t
                USING policy_raw_import r
                JOIN policy p ON p.policy_code = r.serv_id
                WHERE t.policy_id = p.policy_id
                  AND r.list_json IS NOT NULL
                  AND r.detail_json IS NOT NULL
                  AND r.detail_status = 'COMPLETED'
            """,
        )

        return await cls._fetch_count(
            conn,
            """
                WITH checklist_items AS (
                    SELECT
                        p.policy_id,
                        'APPLY_STEP_' || lpad(step_item.ordinality::text, 3, '0')
                            AS item_code,
                        NULLIF(step_item.item->>'servSeDetailNm', '') AS item_label,
                        NULLIF(step_item.item->>'servSeDetailLink', '')
                            AS item_description,
                        TRUE AS is_required,
                        100 + step_item.ordinality::int AS display_order
                    FROM policy_raw_import r
                    JOIN policy p ON p.policy_code = r.serv_id
                    CROSS JOIN LATERAL jsonb_array_elements(
                        COALESCE(r.detail_json->'applmetList', '[]'::jsonb)
                    ) WITH ORDINALITY AS step_item(item, ordinality)
                    WHERE r.list_json IS NOT NULL
                      AND r.detail_json IS NOT NULL
                      AND r.detail_status = 'COMPLETED'

                    UNION ALL

                    SELECT
                        p.policy_id,
                        'FORM_' || lpad(form_item.ordinality::text, 3, '0')
                            AS item_code,
                        NULLIF(form_item.item->>'servSeDetailNm', '') AS item_label,
                        NULLIF(form_item.item->>'servSeDetailLink', '')
                            AS item_description,
                        TRUE AS is_required,
                        200 + form_item.ordinality::int AS display_order
                    FROM policy_raw_import r
                    JOIN policy p ON p.policy_code = r.serv_id
                    CROSS JOIN LATERAL jsonb_array_elements(
                        COALESCE(r.detail_json->'basfrmList', '[]'::jsonb)
                    ) WITH ORDINALITY AS form_item(item, ordinality)
                    WHERE r.list_json IS NOT NULL
                      AND r.detail_json IS NOT NULL
                      AND r.detail_status = 'COMPLETED'
                      AND (
                        form_item.item->>'servSeDetailNm' LIKE '%신청%서%'
                        OR form_item.item->>'servSeDetailNm' LIKE '%동의서%'
                        OR form_item.item->>'servSeDetailNm' LIKE '%확인서%'
                        OR form_item.item->>'servSeDetailNm' LIKE '%위임장%'
                        OR form_item.item->>'servSeDetailNm' LIKE '%진단서%'
                        OR form_item.item->>'servSeDetailNm' LIKE '%증명서%'
                      )
                ),
                normalized_items AS (
                    SELECT
                        (policy_id * 10000 + display_order)::bigint
                            AS template_item_id,
                        policy_id,
                        item_code,
                        item_label,
                        item_description,
                        is_required,
                        display_order
                    FROM checklist_items
                    WHERE item_label IS NOT NULL
                ),
                inserted AS (
                    INSERT INTO policy_checklist_template (
                        template_item_id,
                        policy_id,
                        item_code,
                        item_label,
                        item_description,
                        is_required,
                        display_order,
                        is_active,
                        updated_at
                    )
                    SELECT
                        template_item_id,
                        policy_id,
                        item_code,
                        item_label,
                        item_description,
                        is_required,
                        display_order,
                        TRUE,
                        CURRENT_TIMESTAMP
                    FROM normalized_items
                    RETURNING template_item_id
                )
                SELECT COUNT(*) FROM inserted
            """,
        )

    @classmethod
    async def count_importable_raw_rows(cls, conn) -> int:
        return await cls._fetch_count(
            conn,
            """
                SELECT COUNT(*)
                FROM policy_raw_import
                WHERE list_json IS NOT NULL
                  AND detail_json IS NOT NULL
                  AND detail_status = 'COMPLETED'
            """,
        )
