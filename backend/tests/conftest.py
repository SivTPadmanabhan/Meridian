"""Shared test fixtures.

Tests run against the live Docker Postgres/Redis (the same stack used in dev).
The schema must already be migrated (`alembic upgrade head`).
"""

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.config import settings
from backend.db.session import engine
from backend.main import app

# Deterministic secrets for signature/token tests. The webhook dependencies read
# these off the settings singleton at call time, so patching the attributes works.
settings.GITHUB_WEBHOOK_SECRET = "test-github-secret"
settings.GITLAB_WEBHOOK_SECRET = "test-gitlab-secret"

_TABLES = "events, incidents, agent_runs, eval_results, document_chunks"


@pytest_asyncio.fixture(autouse=True)
async def _reset_connections():
    """Dispose loop-bound connections after each test.

    pytest-asyncio gives each test its own event loop; module-global async pools
    (SQLAlchemy engine, asyncpg pool, redis client) would otherwise carry
    connections bound to a closed loop into the next test.
    """
    yield
    await engine.dispose()
    from backend.rag import pgpool, retriever

    await pgpool.close_pool()
    if retriever._redis is not None:
        await retriever._redis.aclose()
        retriever._redis = None


@pytest_asyncio.fixture
async def clean_db():
    """Truncate all app tables before the test runs."""
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
