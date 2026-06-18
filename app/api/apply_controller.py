from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.services.apply_preparation_service import ApplyPreparationService


router = APIRouter(prefix="/policies", tags=["Apply Preparation"])


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
