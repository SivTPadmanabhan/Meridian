"""Webhook ingest endpoints.

Contract (AD-7): validate signature → store the raw Event → schedule background
processing → return 200 fast. No LLM/graph work happens inline. Invalid
signatures return 401 and store nothing.

Phase 1 background processing = normalize + ingest into document_chunks. Phase 2
extends ``process_event`` to also create the Incident + AgentRun and invoke the
graph.
"""

import hashlib
import hmac
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.integrations.github import parse_github_event
from backend.integrations.gitlab import parse_gitlab_event
from backend.integrations.normalized import NormalizedEvent
from backend.models.event import Event
from backend.rag.ingest import ingest_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def verify_github_signature(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> bytes:
    """Validate the GitHub HMAC-SHA256 signature; return the raw body on success."""
    body = await request.body()
    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="missing signature")
    expected = "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")
    return body


async def verify_gitlab_token(
    x_gitlab_token: str | None = Header(default=None),
) -> None:
    """Validate the GitLab shared-secret token (constant-time equality)."""
    if not x_gitlab_token or not hmac.compare_digest(
        x_gitlab_token, settings.GITLAB_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=401, detail="invalid token")


async def process_event(event_id: uuid.UUID, normalized: NormalizedEvent) -> None:
    """Background processing for an ingested event (Phase 1: RAG ingest only)."""
    try:
        await ingest_event(normalized)
    except Exception:
        logger.exception("background processing failed for event %s", event_id)


async def _store_event(source: str, event_type: str, payload: dict, raw_body: bytes) -> Event:
    event = Event(
        id=uuid.uuid4(),
        source=source,
        event_type=event_type,
        payload=payload,
        raw_body=raw_body.decode("utf-8", errors="replace"),
    )
    async with AsyncSessionLocal() as session:
        session.add(event)
        await session.commit()
        await session.refresh(event)
    return event


@router.post("/github")
async def github_webhook(
    background_tasks: BackgroundTasks,
    body: bytes = Depends(verify_github_signature),
    x_github_event: str = Header(default="unknown"),
) -> dict:
    payload = await _json(body)
    event = await _store_event("github", x_github_event, payload, body)
    normalized = parse_github_event(x_github_event, payload)
    background_tasks.add_task(process_event, event.id, normalized)
    return {"status": "accepted", "event_id": str(event.id)}


@router.post("/gitlab", dependencies=[Depends(verify_gitlab_token)])
async def gitlab_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitlab_event: str = Header(default="unknown"),
) -> dict:
    body = await request.body()
    payload = await _json(body)
    normalized = parse_gitlab_event(x_gitlab_event, payload)
    event = await _store_event("gitlab", normalized.event_type, payload, body)
    background_tasks.add_task(process_event, event.id, normalized)
    return {"status": "accepted", "event_id": str(event.id)}


async def _json(body: bytes) -> dict:
    import json

    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
