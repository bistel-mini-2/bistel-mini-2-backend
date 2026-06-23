import asyncio
from unittest.mock import AsyncMock

from app.repositories.favorite_repository import FavoriteRepository


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class MappingRows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


def test_find_list_counts_and_orders_latest_saved_first() -> None:
    db = AsyncMock()
    db.execute.side_effect = [
        ScalarResult(21),
        MappingRows(
            [
                {
                    "policy_id": 1,
                    "policy_slug": "WLF00000024",
                    "policy_name": "테스트 정책",
                    "category": "생활지원",
                    "region_scope": "NATIONAL",
                    "region_code": None,
                    "saved_at": None,
                }
            ]
        ),
    ]

    rows, total = asyncio.run(
        FavoriteRepository.find_list(
            db,
            user_id=7,
            page=2,
            size=10,
        )
    )

    count_statement = str(db.execute.await_args_list[0].args[0])
    list_statement = db.execute.await_args_list[1].args[0]
    list_sql = str(list_statement)

    assert total == 21
    assert rows[0]["policy_name"] == "테스트 정책"
    assert "count(*)" in count_statement.lower()
    assert "JOIN policy" in count_statement
    assert "policy.is_active IS true" in count_statement
    assert "user_favorites.saved_at DESC" in list_sql
    assert list_statement.compile().params["param_1"] == 10
    assert list_statement.compile().params["param_2"] == 10
