from typing import Annotated

from fastapi import Depends, status
from fastapi.security import OAuth2PasswordBearer

from app.common.exceptions import AppException, ErrorCode
from app.core.security import decode_access_token
from app.db.models.user import User
from app.db.session import DbSessionDep
from app.services.auth_service import AuthService


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_current_user(
    db: DbSessionDep,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    payload = decode_access_token(token)
    subject = payload.get("sub")

    if subject is None:
        raise AppException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Invalid authentication credentials",
        )

    try:
        user_id = int(subject)
    except ValueError as exc:
        raise AppException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Invalid authentication credentials",
        ) from exc

    return await AuthService.get_current_user_by_id(db, user_id)


CurrentUserDep = Annotated[User, Depends(get_current_user)]

__all__ = ["DbSessionDep", "CurrentUserDep", "get_current_user"]
