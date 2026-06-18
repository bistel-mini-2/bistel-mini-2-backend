from psycopg_pool import AsyncConnectionPool

from app.core.config import settings


psycopg_pool = AsyncConnectionPool(
    settings.psycopg_database_url,
    min_size=1,
    max_size=5,
    kwargs={"autocommit": True},
    open=False,
)
