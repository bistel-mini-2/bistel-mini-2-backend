import asyncio
from datetime import datetime
from unittest.mock import ANY, AsyncMock

import pytest

from app.common.exceptions import AppException, ErrorCode
from app.db.models.policy import Policy
from app.db.models.user_favorite import UserFavorite
from app.repositories.favorite_repository import FavoriteRepository
from app.services.favorite_service import FavoriteService


def make_policy() -> Policy:
    return Policy(
        policy_id=1,
        policy_code="WLF00000024",
        policy_name="테스트 정책",
        main_category="생활지원",
        region_scope="NATIONAL",
        is_active=True,
    )


def test_add_favorite_saves_policy(monkeypatch) -> None:
    policy = make_policy()
    saved_at = datetime(2026, 6, 22, 12, 0, 0)
    find_policy = AsyncMock(return_value=policy)
    find_favorite = AsyncMock(return_value=None)

    async def fake_save(db, favorite: UserFavorite) -> UserFavorite:
        favorite.saved_at = saved_at
        return favorite

    monkeypatch.setattr(
        FavoriteRepository,
        "find_policy_by_slug",
        find_policy,
    )
    monkeypatch.setattr(
        FavoriteRepository,
        "find_favorite",
        find_favorite,
    )
    monkeypatch.setattr(FavoriteRepository, "save", fake_save)

    response = asyncio.run(
        FavoriteService.add(
            object(),  # type: ignore[arg-type]
            user_id=7,
            policy_slug=" WLF00000024 ",
        )
    )

    assert response.policy_id == "1"
    assert response.policy_slug == "WLF00000024"
    assert response.region == "national"
    assert response.saved_at == saved_at
    find_policy.assert_awaited_once_with(
        ANY,
        "WLF00000024",
    )


def test_add_favorite_rejects_duplicate(monkeypatch) -> None:
    monkeypatch.setattr(
        FavoriteRepository,
        "find_policy_by_slug",
        AsyncMock(return_value=make_policy()),
    )
    monkeypatch.setattr(
        FavoriteRepository,
        "find_favorite",
        AsyncMock(return_value=UserFavorite(user_id=7, policy_id=1)),
    )

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            FavoriteService.add(
                object(),  # type: ignore[arg-type]
                user_id=7,
                policy_slug="WLF00000024",
            )
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == ErrorCode.CONFLICT


def test_remove_favorite_rejects_missing_saved_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        FavoriteRepository,
        "find_policy_by_slug",
        AsyncMock(return_value=make_policy()),
    )
    monkeypatch.setattr(
        FavoriteRepository,
        "find_favorite",
        AsyncMock(return_value=None),
    )

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            FavoriteService.remove(
                object(),  # type: ignore[arg-type]
                user_id=7,
                policy_slug="WLF00000024",
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_favorite_action_rejects_unknown_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        FavoriteRepository,
        "find_policy_by_slug",
        AsyncMock(return_value=None),
    )

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            FavoriteService.add(
                object(),  # type: ignore[arg-type]
                user_id=7,
                policy_slug="UNKNOWN",
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == ErrorCode.POLICY_NOT_FOUND


def test_get_list_maps_policy_fields(monkeypatch) -> None:
    find_list = AsyncMock(
        return_value=(
            [
                {
                    "policy_id": 1,
                    "policy_slug": "WLF00000024",
                    "policy_name": "테스트 정책",
                    "category": "생활지원",
                    "region_scope": "LOCAL",
                    "region_code": "seoul",
                    "saved_at": datetime(2026, 6, 22, 12, 0, 0),
                }
            ],
            1,
        )
    )
    monkeypatch.setattr(FavoriteRepository, "find_list", find_list)

    items, total = asyncio.run(
        FavoriteService.get_list(
            object(),  # type: ignore[arg-type]
            user_id=7,
            page=1,
            size=20,
        )
    )

    assert total == 1
    assert items[0].policy_id == "1"
    assert items[0].policy_slug == "WLF00000024"
    assert items[0].region == "seoul"
