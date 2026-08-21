import asyncpg
from typing import Optional
from app.config import settings

_pool: Optional[asyncpg.Pool] = None


async def init_db() -> asyncpg.Pool:
    global _pool
    if _pool is None or getattr(_pool, "_closed", False):
        _pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=1,
            max_size=10
        )
    return _pool


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_db_pool() -> asyncpg.Pool:
    if _pool is None or getattr(_pool, "_closed", False):
        raise RuntimeError("Database connection pool is not initialized.")
    return _pool
