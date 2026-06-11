"""Salesforce RevOps integration (V2).

Two responsibilities:
  * ``parse_salesforce_event`` — normalize an inbound Salesforce notification
    (platform event / outbound message JSON) into the shared ``NormalizedEvent``
    with ``source='salesforce'``, so RevOps signals flow through the same triage
    pipeline as DevOps events.
  * ``refresh_access_token`` — exchange the stored refresh token for a fresh
    access token via the OAuth ``refresh_token`` grant. Returns ``None`` when
    unconfigured (no-keys policy).

Parsing is defensive: missing fields fall back to sensible defaults so an
unexpected payload never 500s the webhook (AD-7).
"""

import logging
from datetime import datetime, timezone

import httpx

from backend.config import settings
from backend.integrations.normalized import NormalizedEvent

logger = logging.getLogger(__name__)


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def parse_salesforce_event(payload: dict) -> NormalizedEvent:
    sobject = payload.get("sobject") or "Record"
    event_type = payload.get("event_type") or f"{sobject.lower()}.changed"
    account = payload.get("account") or payload.get("account_name") or "salesforce"
    subject = payload.get("subject") or payload.get("name") or f"{sobject} update"
    status = payload.get("status") or payload.get("stage") or ""
    title = f"{sobject}: {subject}" + (f" ({status})" if status else "")
    body = payload.get("description") or payload.get("body") or ""
    occurred_at = _parse_ts(payload.get("created_date") or payload.get("last_modified_date"))
    return NormalizedEvent(
        source="salesforce",
        event_type=event_type,
        repo=account,
        title=title,
        body_text=body,
        occurred_at=occurred_at,
        raw=payload,
    )


async def refresh_access_token() -> str | None:
    """Exchange the refresh token for a fresh access token (OAuth refresh grant).

    Returns ``None`` (with a warning) when OAuth credentials are absent, so a
    no-key environment never raises.
    """
    if not (
        settings.SALESFORCE_CLIENT_ID
        and settings.SALESFORCE_CLIENT_SECRET
        and settings.SALESFORCE_REFRESH_TOKEN
    ):
        logger.warning("Salesforce OAuth not configured; cannot refresh token")
        return None
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            settings.SALESFORCE_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": settings.SALESFORCE_CLIENT_ID,
                "client_secret": settings.SALESFORCE_CLIENT_SECRET,
                "refresh_token": settings.SALESFORCE_REFRESH_TOKEN,
            },
        )
        resp.raise_for_status()
        return resp.json().get("access_token")
