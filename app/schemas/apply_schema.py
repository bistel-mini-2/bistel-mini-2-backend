from pydantic import BaseModel, ConfigDict


class ChecklistItem(BaseModel):
    id: str
    label: str
    done: bool


class ApplyPreparationResponse(BaseModel):
    apply_id: str | None
    saved: bool
    policy_id: str
    how_to_apply: str | None
    contact: str | None
    official_url: str | None
    checklist: list[ChecklistItem]
    caution: str | None
    progress_percent: int

    model_config = ConfigDict(from_attributes=True)


class ChecklistItemPatchRequest(BaseModel):
    done: bool


class ChecklistItemPatchResponse(BaseModel):
    item: ChecklistItem
    progress_percent: int
