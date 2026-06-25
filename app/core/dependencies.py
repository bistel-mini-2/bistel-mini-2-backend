from typing import Annotated

from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.common.exceptions import AppException, ErrorCode
from app.core.security import decode_access_token
from app.db.models.user import User
from app.db.session import DbSessionDep
from app.services.auth_service import AuthService


bearer_scheme = HTTPBearer(
    scheme_name="BearerAuth",
    description="JWT access token issued by /api/v1/auth/login",
    auto_error=False,
)


async def get_current_user(
    db: DbSessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> User:
    if credentials is None:
        raise AppException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="Invalid authentication credentials",
        )

    payload = decode_access_token(credentials.credentials)
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


async def get_optional_current_user(
    db: DbSessionDep,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(bearer_scheme),
    ],
) -> User | None:
    if credentials is None:
        return None

    payload = decode_access_token(credentials.credentials)
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
OptionalCurrentUserDep = Annotated[User | None, Depends(get_optional_current_user)]

__all__ = [
    "DbSessionDep",
    "CurrentUserDep",
    "OptionalCurrentUserDep",
    "get_current_user",
    "get_optional_current_user",
]
