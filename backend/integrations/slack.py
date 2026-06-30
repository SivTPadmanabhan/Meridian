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
from slack_sdk.webhook.async_client import AsyncWebhookClient

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


def build_decision_feedback(
    original_blocks: list[dict], decision: str, user_id: str | None
) -> dict:
    """Rebuild an alert after a decision: drop the Approve/Dismiss buttons and
    append a banner showing the outcome and who decided. Used to replace the
    original Slack message via its ``response_url`` so a click gives visible
    feedback (AD-1 stays a DB update; this only reflects it back to the channel).
    """
    verb = "Approved" if decision == "approved" else "Dismissed"
    icon = "✅" if decision == "approved" else "🚫"
    who = f" by <@{user_id}>" if user_id else ""
    when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    kept = [b for b in original_blocks if b.get("type") != "actions"]
    kept.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"{icon} *{verb}*{who} · {when}"}],
        }
    )
    return {"blocks": kept, "text": f"Incident {decision}"}


async def post_decision_feedback(
    response_url: str, original_blocks: list[dict], decision: str, user_id: str | None
) -> bool:
    """Replace the original Slack alert with a decision banner via ``response_url``.

    ``response_url`` is a short-lived, signed callback Slack hands us with each
    interaction — it needs no bot scope. Returns whether Slack accepted the
    update; callers treat failure as non-fatal (the decision is already stored).
    """
    message = build_decision_feedback(original_blocks, decision, user_id)
    resp = await AsyncWebhookClient(response_url).send(
        replace_original=True, text=message["text"], blocks=message["blocks"]
    )
    return resp.status_code == 200


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


async def fetch_channel_history(
    channel_id: str | None = None, limit: int | None = None
) -> list[str]:
    """Return the text of recent messages in the ingest channel (V2).

    Skips messages without usable text (joins, bot housekeeping). Returns ``[]``
    when Slack is unconfigured so a no-key environment never raises.
    """
    channel = channel_id or settings.SLACK_INGEST_CHANNEL_ID
    if not settings.SLACK_BOT_TOKEN or not channel:
        logger.warning("Slack ingest not configured (token/channel empty); skipping")
        return []
    resp = await _get_client().conversations_history(
        channel=channel, limit=limit or settings.SLACK_INGEST_LIMIT
    )
    messages = resp["messages"] or []
    return [m["text"] for m in messages if m.get("text")]


async def ingest_slack_history(channel_id: str | None = None) -> int:
    """Poll the ingest channel and embed its content into ``document_chunks``.

    Imported lazily to avoid a heavy RAG import at module load. Returns the
    number of chunks stored (0 when unconfigured or the channel is empty).
    """
    texts = await fetch_channel_history(channel_id)
    if not texts:
        return 0
    from backend.rag import ingest

    document = "\n\n".join(texts)
    return await ingest.ingest_text("slack", document)


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
