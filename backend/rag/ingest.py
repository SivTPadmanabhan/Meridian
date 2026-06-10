"""Ingest pipeline: text → chunks → embeddings → document_chunks.

Used both by the webhook background task (ingesting live NormalizedEvents) and
by the ``--seed`` CLI (ingesting historical incidents for the RAG knowledge base).
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from backend.integrations.normalized import NormalizedEvent
from backend.rag.embedder import embed
from backend.rag.pgpool import close_pool, get_pool, to_vector

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500  # target characters per chunk
SEED_FILE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "seed_incidents.jsonl"


def chunk_text(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Split text into ~``size``-char chunks on whitespace, never mid-word."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for word in words:
        added = len(word) + (1 if current else 0)
        if current and length + added > size:
            chunks.append(" ".join(current))
            current = [word]
            length = len(word)
        else:
            current.append(word)
            length += added
    if current:
        chunks.append(" ".join(current))
    return chunks


async def ingest_text(source: str, text: str) -> int:
    """Chunk, embed, and upsert raw text. Returns the number of chunks stored."""
    chunks = chunk_text(text)
    if not chunks:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for chunk in chunks:
                vector = to_vector(await embed(chunk))
                await conn.execute(
                    "INSERT INTO document_chunks (source, content, embedding) "
                    "VALUES ($1, $2, $3)",
                    source,
                    chunk,
                    vector,
                )
    logger.info("ingested %d chunk(s) from source=%s", len(chunks), source)
    return len(chunks)


async def ingest_event(event: NormalizedEvent) -> int:
    return await ingest_text(event.source, event.to_document_text())


async def seed(path: Path = SEED_FILE) -> int:
    """Ingest the historical-incident seed file (one JSON object per line)."""
    total = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            source = record.get("source", "seed")
            text = record.get("content") or record.get("text", "")
            total += await ingest_text(source, text)
    logger.info("seed complete: %d chunk(s) from %s", total, path)
    return total


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Meridian RAG ingest")
    parser.add_argument("--seed", action="store_true", help="ingest the seed incidents file")
    args = parser.parse_args()
    logging.basicConfig(level="INFO")
    try:
        if args.seed:
            await seed()
        else:
            parser.error("nothing to do — pass --seed")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(_main())
