import json
from typing import Any

# profile 파생 rule을 구분하는 origin 값(서비스와 공유).
ORIGIN_CONDITION_PROFILE = "condition_profile"

# replace_rules_for_policy INSERT 컬럼 순서.
_RULE_COLUMNS = (
    "rule_type",
    "operator",
    "field_name",
    "value_json",
    "is_hard_filter",
    "manual_check_required",
    "manual_check_reason",
    "note",
    "rule_group",
    "group_operator",
    "source_text",
    "confidence",
    "review_required",
    "is_exclusion",
)


class PolicyRuleIngestRepository:
    @staticmethod
    async def ensure_schema(conn) -> None:
        """policy_rule 테이블과 #154 컬럼(origin 포함)을 보장한다.

        정식 마이그레이션(154_*.sql, 148_*.sql)이 적용된 환경에서는 모두 IF NOT EXISTS로
        no-op이며, 신규/테스트 환경을 위해 최소 스키마를 멱등 보장한다.
        """
        statements = [
            """
                CREATE TABLE IF NOT EXISTS policy_rule (
                    rule_id bigserial PRIMARY KEY,
                    policy_id bigint NOT NULL
                        REFERENCES policy(policy_id) ON DELETE CASCADE,
                    rule_type varchar(50) NOT NULL,
                    operator varchar(30) NOT NULL,
                    field_name varchar(100) NOT NULL,
                    value_json jsonb NOT NULL,
                    is_hard_filter boolean NOT NULL DEFAULT true,
                    manual_check_required boolean NOT NULL DEFAULT false,
                    manual_check_reason text,
                    note text,
                    rule_group varchar(100) NOT NULL DEFAULT 'ALL',
                    group_operator varchar(10) NOT NULL DEFAULT 'AND',
                    source_text text,
                    confidence numeric(5, 4),
                    review_required boolean NOT NULL DEFAULT false,
                    is_exclusion boolean NOT NULL DEFAULT false,
                    origin varchar(30) NOT NULL DEFAULT 'openapi'
                )
            """,
            "ALTER TABLE policy_rule ADD COLUMN IF NOT EXISTS rule_group varchar(100) NOT NULL DEFAULT 'ALL'",
            "ALTER TABLE policy_rule ADD COLUMN IF NOT EXISTS group_operator varchar(10) NOT NULL DEFAULT 'AND'",
            "ALTER TABLE policy_rule ADD COLUMN IF NOT EXISTS source_text text",
            "ALTER TABLE policy_rule ADD COLUMN IF NOT EXISTS confidence numeric(5, 4)",
            "ALTER TABLE policy_rule ADD COLUMN IF NOT EXISTS review_required boolean NOT NULL DEFAULT false",
            "ALTER TABLE policy_rule ADD COLUMN IF NOT EXISTS is_exclusion boolean NOT NULL DEFAULT false",
            "ALTER TABLE policy_rule ADD COLUMN IF NOT EXISTS origin varchar(30) NOT NULL DEFAULT 'openapi'",
            """
                CREATE INDEX IF NOT EXISTS policy_rule_policy_origin_idx
                ON policy_rule (policy_id, origin)
            """,
        ]
        async with conn.cursor() as cur:
            for statement in statements:
                await cur.execute(statement)

    @staticmethod
    async def find_rule_targets(
        conn,
        limit: int,
        overwrite: bool = False,
    ) -> list[dict[str, Any]]:
        """condition_json이 저장된 정책을 파생 대상으로 조회한다.

        overwrite=False면 이미 profile 파생 rule이 존재하는 정책은 건너뛴다.
        """
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    SELECT
                        cp.policy_id,
                        p.policy_code,
                        p.policy_name,
                        cp.condition_json,
                        cp.source_text,
                        cp.confidence,
                        cp.review_required
                    FROM policy_condition_profile cp
                    JOIN policy p ON p.policy_id = cp.policy_id
                    WHERE p.is_active = TRUE
                      AND (
                        %s::boolean = TRUE
                        OR NOT EXISTS (
                            SELECT 1 FROM policy_rule pr
                            WHERE pr.policy_id = cp.policy_id
                              AND pr.origin = %s
                        )
                      )
                    ORDER BY cp.policy_id
                    LIMIT %s
                """,
                (overwrite, ORIGIN_CONDITION_PROFILE, limit),
            )
            rows = await cur.fetchall()

        columns = [
            "policy_id",
            "policy_code",
            "policy_name",
            "condition_json",
            "source_text",
            "confidence",
            "review_required",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    @staticmethod
    async def replace_rules_for_policy(
        conn,
        *,
        policy_id: int,
        rules: list[dict[str, Any]],
    ) -> int:
        """해당 정책의 기존 rule을 모두 제거하고 profile 파생 rule로 교체한다.

        profile은 OpenAPI 원천보다 신뢰 가능한 소스이므로, 파생 대상 정책에서는
        profile 파생 rule을 단일 진실로 둔다(이중 AND 적용 방지). origin 컬럼으로
        후속 충돌 정리(예: SQL 추출이 다시 채우지 않도록 제외) 시 식별 가능하다.
        """
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM policy_rule WHERE policy_id = %s",
                (policy_id,),
            )
            if not rules:
                return 0

            await cur.executemany(
                f"""
                    INSERT INTO policy_rule (
                        policy_id,
                        {", ".join(_RULE_COLUMNS)},
                        origin
                    )
                    VALUES (
                        %(policy_id)s,
                        %(rule_type)s,
                        %(operator)s,
                        %(field_name)s,
                        %(value_json)s::jsonb,
                        %(is_hard_filter)s,
                        %(manual_check_required)s,
                        %(manual_check_reason)s,
                        %(note)s,
                        %(rule_group)s,
                        %(group_operator)s,
                        %(source_text)s,
                        %(confidence)s,
                        %(review_required)s,
                        %(is_exclusion)s,
                        %(origin)s
                    )
                """,
                [
                    {
                        "policy_id": policy_id,
                        "rule_type": rule["rule_type"],
                        "operator": rule["operator"],
                        "field_name": rule["field_name"],
                        "value_json": json.dumps(
                            rule["value_json"], ensure_ascii=False
                        ),
                        "is_hard_filter": rule["is_hard_filter"],
                        "manual_check_required": rule["manual_check_required"],
                        "manual_check_reason": rule["manual_check_reason"],
                        "note": rule["note"],
                        "rule_group": rule["rule_group"],
                        "group_operator": rule["group_operator"],
                        "source_text": rule["source_text"],
                        "confidence": rule["confidence"],
                        "review_required": rule["review_required"],
                        "is_exclusion": rule["is_exclusion"],
                        "origin": ORIGIN_CONDITION_PROFILE,
                    }
                    for rule in rules
                ],
            )
        return len(rules)
