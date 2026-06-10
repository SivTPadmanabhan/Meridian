"""Sentence-transformers embedding wrapper.

The model is loaded once at import. ``encode`` is blocking (CPU/torch), so it is
always wrapped in ``asyncio.to_thread`` to stay off the event loop.
"""

import asyncio
import logging

from sentence_transformers import SentenceTransformer

from backend.config import settings

logger = logging.getLogger(__name__)

# Loaded once per process. Output dimension is 384 (all-MiniLM-L6-v2) →
# document_chunks.embedding is vector(384).
_model = SentenceTransformer(settings.EMBEDDING_MODEL)


async def embed(text: str) -> list[float]:
    vector = await asyncio.to_thread(_model.encode, text)
    return vector.tolist()
