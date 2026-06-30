import asyncio

from app.repositories.compare_repository import CompareRepository


class _FakeMappings:
    def all(self):
        return []


class _FakeResult:
    def mappings(self):
        return _FakeMappings()


class _FakeDb:
    def __init__(self):
        self.statement = ""

    async def execute(self, statement, params=None):
        self.statement = str(statement)
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
    assert "COALESCE(rd.source_type, 'REQUIRED') <> 'POLICY_REFERENCE'" in db.statement
