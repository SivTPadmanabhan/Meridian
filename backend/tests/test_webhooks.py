"""Webhook endpoint tests: signature validation + Event persistence (AD-7)."""

import hashlib
import hmac
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.models.event import Event
from backend.routes import webhooks

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_background_ingest(monkeypatch):
    """Skip the real embed/ingest in webhook tests — keep them fast and DB-light."""
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(webhooks, "process_event", _noop)


def _github_sig(body: bytes) -> str:
    return "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()


async def _event_count() -> int:
    async with AsyncSessionLocal() as session:
        return await session.scalar(select(func.count()).select_from(Event))


@pytest.mark.asyncio
async def test_github_valid_signature_stores_event(client, clean_db) -> None:
    body = (FIXTURES / "github_ci_failure.json").read_bytes()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _github_sig(body),
            "X-GitHub-Event": "check_run",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert await _event_count() == 1


@pytest.mark.asyncio
async def test_github_invalid_signature_rejected(client, clean_db) -> None:
    body = (FIXTURES / "github_push.json").read_bytes()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": "sha256=deadbeef",
            "X-GitHub-Event": "push",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert await _event_count() == 0


@pytest.mark.asyncio
async def test_gitlab_valid_token_stores_event(client, clean_db) -> None:
    body = (FIXTURES / "gitlab_pipeline.json").read_bytes()
    resp = await client.post(
        "/webhooks/gitlab",
        content=body,
        headers={
            "X-Gitlab-Token": settings.GITLAB_WEBHOOK_SECRET,
            "X-Gitlab-Event": "Pipeline Hook",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert await _event_count() == 1


@pytest.mark.asyncio
async def test_gitlab_invalid_token_rejected(client, clean_db) -> None:
    body = (FIXTURES / "gitlab_pipeline.json").read_bytes()
    resp = await client.post(
        "/webhooks/gitlab",
        content=body,
        headers={
            "X-Gitlab-Token": "wrong-token",
            "X-Gitlab-Event": "Pipeline Hook",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert await _event_count() == 0
