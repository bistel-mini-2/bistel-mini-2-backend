from datetime import datetime

from pydantic import BaseModel


class FavoriteCreateResponse(BaseModel):
    policy_id: str
    policy_slug: str
    policy_name: str
    category: str | None = None
    region: str | None = None
    saved_at: datetime


class FavoritePolicyResponse(BaseModel):
    policy_id: str
    policy_slug: str
    policy_name: str
    category: str | None = None
    region: str | None = None
    saved_at: datetime


class FavoriteDeleteResponse(BaseModel):
    policy_slug: str
    removed: bool


class FavoriteListResponse(BaseModel):
    items: list[FavoritePolicyResponse]
