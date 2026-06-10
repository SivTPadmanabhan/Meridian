"""Retriever test: seed historical incidents, query a known topic, expect a hit."""

import pytest

from backend.rag import ingest, retriever
from backend.rag.pgpool import get_pool


@pytest.mark.asyncio
async def test_known_topic_in_top_3(clean_db) -> None:
    chunks = await ingest.seed()
    assert chunks >= 10  # 10 seed incidents, each ≥ 1 chunk

    pool = await get_pool()
    async with pool.acquire() as conn:
        stored = await conn.fetchval("SELECT COUNT(*) FROM document_chunks")
    assert stored == chunks

    results = await retriever.retrieve(
        "payment gateway connection timeout after payment SDK upgrade", k=3
    )
    assert len(results) <= 3 and results
    joined = " ".join(results).lower()
    assert "payment" in joined and "timeout" in joined
