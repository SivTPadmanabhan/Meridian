"""Phase 8 — request-size limit.

AC: a webhook payload over the cap returns 413 and stores nothing. The size guard
runs in the webhook path before signature validation and before the raw Event is
persisted, so an oversized body is rejected cheaply and leaves no DB trace.
"""

from sqlalchemy import text

from backend.config import settings
from backend.db.session import engine


async def test_oversized_webhook_returns_413_and_stores_nothing(client, clean_db):
    original = settings.MAX_WEBHOOK_BODY_BYTES
    settings.MAX_WEBHOOK_BODY_BYTES = 500
    try:
        oversized = b'{"payload":"' + b"A" * 2000 + b'"}'
        resp = await client.post(
            "/webhooks/github",
            content=oversized,
            headers={"X-GitHub-Event": "push", "Content-Type": "application/json"},
        )
        assert resp.status_code == 413, resp.text

        async with engine.connect() as conn:
            count = (await conn.execute(text("SELECT COUNT(*) FROM events"))).scalar_one()
        assert count == 0, f"expected no Event rows, found {count}"
    finally:
        settings.MAX_WEBHOOK_BODY_BYTES = original
