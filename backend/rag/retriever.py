"""pgvector similarity search with a Redis query-embedding cache.

The Redis cache stores the *query embedding* (not search results): a cache hit
skips the sentence-transformers encoder, but the pgvector search always runs so
results reflect the current contents of document_chunks.
"""

import hashlib
import json
import logging

import redis.asyncio as aioredis

from backend.config import settings
from backend.rag.embedder import embed
from backend.rag.pgpool import get_pool, to_vector

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def _cache_key(query: str) -> str:
    return "qemb:" + hashlib.sha256(query.encode("utf-8")).hexdigest()


async def _query_embedding(query: str) -> list[float]:
    """Embed the query, using the Redis cache to skip the encoder on a hit."""
    key = _cache_key(query)
    client = _get_redis()
    cached = await client.get(key)
    if cached is not None:
        logger.info("query embedding cache lookup", extra={"cache_hit": True})
        return json.loads(cached)
    logger.info("query embedding cache lookup", extra={"cache_hit": False})
    embedding = await embed(query)
    await client.set(key, json.dumps(embedding), ex=settings.REDIS_CACHE_TTL)
    return embedding


async def retrieve(query: str, k: int = 5) -> list[str]:
    """Return the ``k`` most similar chunk texts (cosine distance, ascending)."""
    embedding = await _query_embedding(query)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT content FROM document_chunks "
            "ORDER BY embedding <=> $1 LIMIT $2",
            to_vector(embedding),
            k,
        )
    return [row["content"] for row in rows]
