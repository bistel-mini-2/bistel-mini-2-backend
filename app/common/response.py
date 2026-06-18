import math
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.common.schemas import ApiResponse, PaginationMeta


def success_response(data: Any = None, status_code: int = 200) -> JSONResponse:
    body = ApiResponse(success=True, data=jsonable_encoder(data), meta={})
    return JSONResponse(status_code=status_code, content=body.model_dump())


def paginated_response(
    data: Any,
    page: int,
    size: int,
    total: int,
    status_code: int = 200,
) -> JSONResponse:
    meta = PaginationMeta(
        page=page,
        size=size,
        total=total,
        total_pages=math.ceil(total / size) if size > 0 else 0,
    )
    body = ApiResponse(
        success=True,
        data=jsonable_encoder(data),
        meta=meta.model_dump(),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())
