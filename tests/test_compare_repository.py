import asyncio

from app.repositories.compare_repository import CompareRepository


class _FakeMappings:
    def all(self):
        return []


class _FakeResult:
    def mappings(self):
        return _FakeMappings()

    def scalar_one(self):
        return 0


class _FakeDb:
    def __init__(self):
        self.statement = ""
        self.statements = []

    async def execute(self, statement, params=None):
        self.statement = str(statement)
        self.statements.append(self.statement)
        self.params = params
        return _FakeResult()


def test_find_policies_by_slugs_excludes_reference_documents() -> None:
    db = _FakeDb()

    asyncio.run(
        CompareRepository.find_policies_by_slugs(
            db,  # type: ignore[arg-type]
            ["WLF00000001", "WLF00000002"],
        )
    )

    assert "FROM required_document rd" in db.statement
    assert "COALESCE(rd.required_type, 'REQUIRED') = 'REQUIRED'" in db.statement
    assert "rd.document_name LIKE '%신청%서%'" in db.statement


def test_compare_history_schema_adds_policy_snapshot_columns() -> None:
    db = _FakeDb()

    asyncio.run(
        CompareRepository.ensure_compare_history_schema(
            db,  # type: ignore[arg-type]
        )
    )

    combined_sql = "\n".join(db.statements)
    assert "selection_guide text" in combined_sql
    assert "policy_slug varchar(100)" in combined_sql
    assert "policy_name varchar(255)" in combined_sql


def test_find_compare_history_prefers_policy_snapshot_values() -> None:
    db = _FakeDb()

    asyncio.run(
        CompareRepository.find_compare_history(
            db,  # type: ignore[arg-type]
            user_id=7,
            page=1,
            size=10,
        )
    )

    combined_sql = "\n".join(db.statements)
    assert "COALESCE(chi.policy_name, p.policy_name)" in combined_sql
    assert "COALESCE(chi.policy_slug, p.policy_code)" in combined_sql
    assert "selection_guide" in combined_sql
    assert "LEFT JOIN policy p ON p.policy_id = chi.policy_id" in combined_sql
