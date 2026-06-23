"""Phase 8 — inbound rate limiting (slowapi, Redis-backed).

AC: a burst on POST /incidents/{id}/approve gets 429; a signed webhook burst
still 200s. Runs against the live Docker Redis (the limiter's storage), like the
rest of the suite. The limiter's per-IP counters are flushed before each test so
results are deterministic regardless of what ran earlier in the window.
"""

import hashlib
import hmac
import uuid
from pathlib import Path

import pytest_asyncio
import redis.asyncio as aioredis

from backend.config import settings
from backend.routes import webhooks

_PUSH_FIXTURE = Path(__file__).parent / "fixtures" / "github_push.json"


@pytest_asyncio.fixture
async def flush_limiter():
    """Clear slowapi/limits counters (Redis) so each test starts at zero."""
    client = aioredis.from_url(settings.REDIS_URL)
    await client.flushdb()
    await client.aclose()
    yield


async def test_approve_burst_gets_429_with_retry_after(client, flush_limiter):
    """The tight human-endpoint limit returns 429 + Retry-After once exceeded."""
    n = int(settings.RATE_LIMIT_APPROVE.split("/")[0])  # allowed requests / window
    responses = [
        await client.post(
            f"/incidents/{uuid.uuid4()}/approve", json={"decision": "approved"}
        )
        for _ in range(n + 3)
    ]
    codes = [r.status_code for r in responses]
    # The first `n` pass the limit (404 — random incident id, body runs); the rest 429.
    assert all(c != 429 for c in codes[:n]), codes
    assert all(c == 429 for c in codes[n:]), codes
    throttled = next(r for r in responses if r.status_code == 429)
    assert "retry-after" in {k.lower() for k in throttled.headers}, dict(throttled.headers)


async def test_signed_webhook_burst_stays_200(client, flush_limiter, monkeypatch):
    """A burst of valid signed GitHub webhooks is never 429'd (generous limit)."""
    # The pipeline is exercised elsewhere; here we isolate the rate limiter.
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(webhooks, "process_event", _noop)

    body = _PUSH_FIXTURE.read_bytes()
    signature = "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    headers = {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Event": "push",
        "Content-Type": "application/json",
    }

    codes = [
        (await client.post("/webhooks/github", content=body, headers=headers)).status_code
        for _ in range(10)
    ]
    assert codes == [200] * 10, codes
