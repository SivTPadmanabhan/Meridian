"""Slack output + interaction signing (Phase 5, AD-1).

``build_alert_message`` renders the Block Kit payload per PRODUCT.md → Slack
Message Structure. ``send_alert`` posts it via the async Slack Web API and
returns the message ``ts`` (or ``None`` when Slack is not configured — the
pipeline degrades gracefully rather than crashing). ``verify_slack_signature``
validates inbound interaction requests (timestamp + HMAC).

Approval itself is a DB update handled by ``apply_decision`` (AD-1); this module
only renders/sends and validates signatures.
"""

import hashlib
import hmac
import logging
import time

from slack_sdk.web.async_client import AsyncWebClient

from backend.config import settings
from backend.models.agent_run import AgentRun
from backend.models.incident import Incident

logger = logging.getLogger(__name__)

APPROVE_ACTION_ID = "approve_action"
DISMISS_ACTION_ID = "dismiss_action"

_SEVERITY_BADGE = {"P0": "🔴", "P1": "🟠", "P2": "🟡", "P3": "⚪"}
_SLACK_MAX_AGE_SECONDS = 60 * 5  # reject replays older than 5 minutes

_client: AsyncWebClient | None = None


def _get_client() -> AsyncWebClient:
    global _client
    if _client is None:
        _client = AsyncWebClient(token=settings.SLACK_BOT_TOKEN)
    return _client


def build_alert_message(run: AgentRun, incident: Incident) -> dict:
    """Render the Block Kit alert for one analyzed incident (PRODUCT.md structure)."""
    severity = incident.severity or "P3"
    badge = _SEVERITY_BADGE.get(severity, "⚪")
    triage = run.triage_output or {}
    confidence = triage.get("confidence")
    analysis = run.analysis_output or {}
    root_cause = analysis.get("root_cause") or "_(no root cause)_"
    contexts = analysis.get("retrieved_context") or []
    proposal = (run.action_proposed or {}).get("suggested_action") or "_(no action proposed)_"

    when = incident.created_at.strftime("%Y-%m-%d %H:%M UTC")
    context_line = f"*When:* {when}"
    if confidence is not None:
        context_line += f"  ·  *Triage confidence:* {confidence:.0%}"

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{badge} {severity} · {incident.title[:140]}"},
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": context_line}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Root cause*\n{root_cause}"}},
    ]
    if contexts:
        joined = "\n".join(f"• {c[:160]}" for c in contexts[:2])  # top-2 similar incidents
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Similar past incidents*\n{joined}"}}
        )
    blocks.append(
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Suggested action*\n{proposal}"}}
    )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve action"},
                    "style": "primary",
                    "action_id": APPROVE_ACTION_ID,
                    "value": str(incident.id),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss"},
                    "action_id": DISMISS_ACTION_ID,
                    "value": str(incident.id),
                },
            ],
        }
    )
    return {"blocks": blocks, "text": f"{severity} incident: {incident.title}"}


async def send_alert(message: dict) -> str | None:
    """Post the alert to the configured channel; return its ``ts``.

    Returns ``None`` (and logs a warning) when Slack credentials are absent, so a
    no-key dev/test environment never breaks the agent pipeline.
    """
    if not settings.SLACK_BOT_TOKEN or not settings.SLACK_CHANNEL_ID:
        logger.warning("Slack not configured (token/channel empty); skipping alert")
        return None
    resp = await _get_client().chat_postMessage(
        channel=settings.SLACK_CHANNEL_ID,
        blocks=message["blocks"],
        text=message["text"],
    )
    return resp["ts"]


def verify_slack_signature(body: bytes, timestamp: str | None, signature: str | None) -> bool:
    """Validate Slack's ``v0`` request signature (timestamp + HMAC-SHA256)."""
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > _SLACK_MAX_AGE_SECONDS:
            return False
    except ValueError:
        return False
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    expected = "v0=" + hmac.new(
        settings.SLACK_SIGNING_SECRET.encode("utf-8"), basestring, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
