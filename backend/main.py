"""FastAPI application entrypoint.

Phase 0: app skeleton + ``GET /health``. The lifespan is a no-op placeholder;
the LangGraph build (Phase 2) and APScheduler offline-eval job (Phase 4) are
wired in here in later phases.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text

from backend.config import settings
from backend.db.session import engine
from backend.routes import webhooks

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Phase 2: build the compiled LangGraph once and stash on app.state.
    # Phase 4: start the APScheduler weekly offline-eval job (APP_ENV != "test").
    logger.info("Meridian starting up (env=%s)", settings.APP_ENV)
    yield
    await engine.dispose()
    logger.info("Meridian shut down")


app = FastAPI(title="Meridian", version="0.1.0", lifespan=lifespan)
app.include_router(webhooks.router)


class HealthResponse(BaseModel):
    status: str
    db: bool
    redis: bool
    langfuse: bool


async def _check_db() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("health: database check failed")
        return False


async def _check_redis() -> bool:
    client = aioredis.from_url(settings.REDIS_URL)
    try:
        return bool(await client.ping())
    except Exception:
        logger.exception("health: redis check failed")
        return False
    finally:
        await client.aclose()


async def _check_langfuse() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(settings.LANGFUSE_HOST)
        return resp.status_code < 500
    except Exception:
        logger.exception("health: langfuse check failed")
        return False


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    db_ok = await _check_db()
    redis_ok = await _check_redis()
    langfuse_ok = await _check_langfuse()
    all_ok = db_ok and redis_ok and langfuse_ok
    return HealthResponse(
        status="ok" if all_ok else "degraded",
        db=db_ok,
        redis=redis_ok,
        langfuse=langfuse_ok,
    )
