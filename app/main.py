from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


load_dotenv()

from app.api.apply_controller import (
    checklist_router as apply_checklist_router,
    router as apply_router,
)
from app.api.auth_controller import auth_router, users_router
from app.api.family_profile_controller import router as family_profile_router
from app.api.policy_data_controller import router as policy_data_router
from app.api.policy_document_controller import router as policy_document_router
from app.api.policy_import_controller import router as policy_import_router
from app.common.exceptions import register_exception_handlers
from app.common.psycopg_pool_conf import psycopg_pool
from app.core.config import settings
from app.db.session import engine
from app.utils.logger import setup_logging


setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await psycopg_pool.open()
    try:
        yield
    finally:
        await psycopg_pool.close()
        await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(family_profile_router)
app.include_router(policy_data_router)
app.include_router(policy_import_router)
app.include_router(policy_document_router)
app.include_router(apply_router)
app.include_router(apply_checklist_router)
register_exception_handlers(app)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="localhost",
        port=8000,
        reload=True,
        access_log=True,
    )
