import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import sys

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
from app.api.chat_controller import router as chat_router
from app.api.family_profile_controller import router as family_profile_router
from app.api.policy_controller import router as policy_router
from app.api.policy_data_controller import router as policy_data_router
from app.api.policy_document_controller import router as policy_document_router
from app.api.policy_import_controller import router as policy_import_router
from app.api.policy_judgement_controller import router as policy_judgement_router
from app.api.policy_rag_controller import router as policy_rag_router
from app.common.exceptions import register_exception_handlers
from app.common.psycopg_pool_conf import psycopg_pool
from app.core.config import settings
from app.db.session import engine
from app.repositories.policy_repository import PolicyRepository
from app.utils.logger import setup_logging


setup_logging()


def configure_event_loop_policy() -> None:
    if sys.platform != "win32":
        return

    selector_policy = getattr(
        asyncio,
        "WindowsSelectorEventLoopPolicy",
        None,
    )
    if selector_policy is not None:
        asyncio.set_event_loop_policy(selector_policy())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await psycopg_pool.open()
    async with psycopg_pool.connection() as conn:
        await PolicyRepository.ensure_search_indexes(conn)

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
app.include_router(policy_router)
app.include_router(policy_data_router)
app.include_router(policy_import_router)
app.include_router(policy_document_router)
app.include_router(policy_rag_router)
app.include_router(policy_judgement_router)
app.include_router(apply_router)
app.include_router(apply_checklist_router)
app.include_router(chat_router)
register_exception_handlers(app)


if __name__ == "__main__":
    configure_event_loop_policy()
    uvicorn.run(
        "app.main:app",
        host="localhost",
        port=8000,
        reload=True,
        access_log=True,
    )
