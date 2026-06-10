"""Shared asyncpg pool for the RAG layer.

document_chunks is read/written via asyncpg directly (AD-5), with the pgvector
codec registered so Python lists/ndarrays round-trip to the ``vector`` type.
"""

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

from backend.config import settings

_pool: asyncpg.Pool | None = None


def _dsn() -> str:
    # asyncpg wants a plain libpq DSN, not the SQLAlchemy "+asyncpg" form.
    return settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


async def _init_connection(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_dsn(), init=_init_connection)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def to_vector(embedding: list[float]) -> np.ndarray:
    """Encode an embedding for the pgvector asyncpg codec."""
    return np.asarray(embedding, dtype=np.float32)
