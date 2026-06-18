from enum import StrEnum
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.common.schemas import ApiResponse, ErrorDetail


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    DUPLICATE_EMAIL = "DUPLICATE_EMAIL"
    DUPLICATE_NICKNAME = "DUPLICATE_NICKNAME"
    EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"
    NICKNAME_ALREADY_EXISTS = "NICKNAME_ALREADY_EXISTS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    PASSWORD_UNCHANGED = "PASSWORD_UNCHANGED"
    POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
    AI_TIMEOUT = "AI_TIMEOUT"
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"


class AppException(Exception):
    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        details: Any | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


_HTTP_STATUS_TO_ERROR_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.INVALID_INPUT,
    401: ErrorCode.UNAUTHORIZED,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
}


def _error_response(
    status_code: int,
    code: ErrorCode,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    body = ApiResponse(
        success=False,
        data=None,
        error=ErrorDetail(
            code=code,
            message=message,
            details=jsonable_encoder(details) if details is not None else None,
        ),
        meta={},
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def _app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = _HTTP_STATUS_TO_ERROR_CODE.get(
        exc.status_code,
        ErrorCode.INTERNAL_SERVER_ERROR,
    )
    response = _error_response(exc.status_code, code, str(exc.detail))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else {}
    message = str(first_error.get("msg", "Validation error"))
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")

    details = [
        {
            "loc": error.get("loc", []),
            "msg": error.get("msg", "Validation error"),
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()
    ]
    return _error_response(422, ErrorCode.VALIDATION_ERROR, message, details)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(500, ErrorCode.INTERNAL_SERVER_ERROR, "Internal server error")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        AppException,
        _app_exception_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        HTTPException,
        _http_exception_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        RequestValidationError,
        _validation_exception_handler,  # type: ignore[arg-type]
    )
    app.add_exception_handler(Exception, _unhandled_exception_handler)
