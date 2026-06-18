from enum import StrEnum

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.common.schemas import ApiResponse, ErrorDetail


class ErrorCode(StrEnum):
  INVALID_INPUT = "INVALID_INPUT"
  UNAUTHORIZED = "UNAUTHORIZED"
  NOT_FOUND = "NOT_FOUND"
  CONFLICT = "CONFLICT"
  DUPLICATE_EMAIL = "DUPLICATE_EMAIL"
  DUPLICATE_NICKNAME = "DUPLICATE_NICKNAME"
  INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
  POLICY_NOT_FOUND = "POLICY_NOT_FOUND"
  AI_TIMEOUT = "AI_TIMEOUT"
  INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"


class AppException(Exception):
  def __init__(self, status_code: int, code: ErrorCode, message: str) -> None:
    self.status_code = status_code
    self.code = code
    self.message = message


_HTTP_STATUS_TO_ERROR_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.INVALID_INPUT,
    401: ErrorCode.UNAUTHORIZED,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
}


def _error_response(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ApiResponse(success=False, error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def _app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    return _error_response(exc.status_code, exc.code, exc.message)


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code = _HTTP_STATUS_TO_ERROR_CODE.get(exc.status_code, ErrorCode.INTERNAL_SERVER_ERROR)
    response = _error_response(exc.status_code, code, str(exc.detail))
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else {}
    message = first_error.get("msg", "Validation error")
    return _error_response(422, ErrorCode.INVALID_INPUT, message)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return _error_response(500, ErrorCode.INTERNAL_SERVER_ERROR, "Internal server error")


def register_exception_handlers(app: FastAPI) -> None:
  app.add_exception_handler(AppException, _app_exception_handler)  # type: ignore[arg-type]
  app.add_exception_handler(HTTPException, _http_exception_handler)  # type: ignore[arg-type]
  app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]
  app.add_exception_handler(Exception, _unhandled_exception_handler)
