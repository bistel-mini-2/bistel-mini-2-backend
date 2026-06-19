from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.schemas.apply_schema import ChecklistItemPatchRequest
from app.services.apply_preparation_service import ApplyPreparationService


router = APIRouter(prefix="/api/v1/policies", tags=["Apply Preparation"])
checklist_router = APIRouter(prefix="/api/v1/apply", tags=["Apply Checklist"])


@router.post("/{policy_slug}/apply", status_code=status.HTTP_201_CREATED)
async def create_apply_preparation(
    policy_slug: str,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await ApplyPreparationService.create(
        db, user_id=current_user.user_id, policy_slug=policy_slug
    )
    return success_response(data=response, status_code=status.HTTP_201_CREATED)


@router.get("/{policy_slug}/apply")
async def get_apply_preparation(
    policy_slug: str,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await ApplyPreparationService.get(
        db, user_id=current_user.user_id, policy_slug=policy_slug
    )
    return success_response(data=response)


@checklist_router.patch("/{apply_id}/checklist/{item_id}")
async def update_checklist_item(
    apply_id: int,
    item_id: int,
    payload: ChecklistItemPatchRequest,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await ApplyPreparationService.update_checklist_item(
        db,
        user_id=current_user.user_id,
        apply_id=apply_id,
        template_item_id=item_id,
        done=payload.done,
    )
    return success_response(data=response)
