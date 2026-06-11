"""Phase 5 — Slack alert rendering, send-once, and the shared approval service."""

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest
from sqlalchemy import select

from backend.agents import action
from backend.agents.action import action_node
from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.integrations import slack
from backend.integrations.slack import (
    APPROVE_ACTION_ID,
    DISMISS_ACTION_ID,
    build_alert_message,
)
from backend.models.agent_run import AgentRun
from backend.models.event import Event
from backend.models.incident import Incident
from backend.routes.incidents import IncidentNotFound, NotPending, apply_decision


# --------------------------------------------------------------------------- #
# build_alert_message
# --------------------------------------------------------------------------- #
def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(), event_id=uuid.uuid4(), severity="P0", status="open",
        title="Check run 'integration-tests': failure", created_at=datetime.now(timezone.utc),
    )


def _run(incident_id: uuid.UUID) -> AgentRun:
    return AgentRun(
        id=uuid.uuid4(), incident_id=incident_id,
        triage_output={"severity": "P0", "confidence": 0.91},
        analysis_output={"root_cause": "payment SDK timeout", "retrieved_context": ["past A", "past B"]},
        action_proposed={"suggested_action": "Pin the SDK and set timeout=60."},
        human_decision="pending",
    )


def test_build_alert_message_has_text_blocks_and_two_buttons() -> None:
    incident = _incident()
    message = build_alert_message(_run(incident.id), incident)

    assert message["text"] and "P0" in message["text"]
    assert isinstance(message["blocks"], list) and message["blocks"]

    actions = [b for b in message["blocks"] if b["type"] == "actions"]
    assert len(actions) == 1
    buttons = actions[0]["elements"]
    assert [b["action_id"] for b in buttons] == [APPROVE_ACTION_ID, DISMISS_ACTION_ID]
    assert all(b["value"] == str(incident.id) for b in buttons)
    # The proposal and root cause made it into the rendered sections.
    rendered = json.dumps(message["blocks"])
    assert "Pin the SDK" in rendered and "payment SDK timeout" in rendered


# --------------------------------------------------------------------------- #
# apply_decision (shared service) — flips both rows; 404 / 409 paths
# --------------------------------------------------------------------------- #
async def _seed_pending() -> uuid.UUID:
    async with AsyncSessionLocal() as s:
        event = Event(id=uuid.uuid4(), source="github", event_type="check_run", payload={}, raw_body="{}")
        s.add(event)
        await s.flush()
        incident = Incident(id=uuid.uuid4(), event_id=event.id, severity="P0", status="open", title="t")
        s.add(incident)
        await s.flush()
        s.add(AgentRun(id=uuid.uuid4(), incident_id=incident.id, human_decision="pending"))
        await s.commit()
        return incident.id


@pytest.mark.asyncio
async def test_apply_decision_flips_both_rows(clean_db) -> None:
    incident_id = await _seed_pending()
    await apply_decision(incident_id, "approved")

    async with AsyncSessionLocal() as s:
        run = (await s.execute(select(AgentRun).where(AgentRun.incident_id == incident_id))).scalar_one()
        incident = await s.get(Incident, incident_id)
    assert run.human_decision == "approved"
    assert incident.status == "approved"


@pytest.mark.asyncio
async def test_apply_decision_unknown_and_not_pending(clean_db) -> None:
    with pytest.raises(IncidentNotFound):
        await apply_decision(uuid.uuid4(), "approved")

    incident_id = await _seed_pending()
    await apply_decision(incident_id, "dismissed")
    with pytest.raises(NotPending):  # already resolved → no longer pending
        await apply_decision(incident_id, "approved")


# --------------------------------------------------------------------------- #
# REST endpoint: POST /incidents/{id}/approve
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_approve_endpoint_paths(client, clean_db) -> None:
    incident_id = await _seed_pending()

    ok = await client.post(f"/incidents/{incident_id}/approve", json={"decision": "approved"})
    assert ok.status_code == 200 and ok.json()["status"] == "approved"

    conflict = await client.post(f"/incidents/{incident_id}/approve", json={"decision": "approved"})
    assert conflict.status_code == 409

    missing = await client.post(f"/incidents/{uuid.uuid4()}/approve", json={"decision": "dismissed"})
    assert missing.status_code == 404


# --------------------------------------------------------------------------- #
# action_node sends exactly one Slack alert per analyzed run
# --------------------------------------------------------------------------- #
class _FakeChat:
    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return type("M", (), {"content": "Pin the SDK and set timeout=60."})()


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat_postMessage(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return {"ts": "1700000000.000100"}


@pytest.mark.asyncio
async def test_action_node_sends_one_alert(monkeypatch, clean_db) -> None:
    incident_id = await _seed_pending()
    monkeypatch.setattr(action, "_get_llm", lambda: _FakeChat())
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(settings, "SLACK_CHANNEL_ID", "C123")
    recorder = _RecordingClient()
    monkeypatch.setattr(slack, "_get_client", lambda: recorder)

    state = {
        "event_id": str(uuid.uuid4()), "incident_id": str(incident_id),
        "event_payload": {"title": "CI failure"}, "severity": "P0", "confidence": 0.9,
        "retrieved_context": ["ctx"], "root_cause": "SDK timeout",
        "suggested_action": "", "eval_scores": {}, "error": None,
    }
    out = await action_node(state)

    assert out["suggested_action"] == "Pin the SDK and set timeout=60."
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["channel"] == "C123"


# --------------------------------------------------------------------------- #
# Slack actions webhook: valid signature flips rows; bad signature → 401
# --------------------------------------------------------------------------- #
def _slack_post_body(incident_id: uuid.UUID, action_id: str) -> bytes:
    payload = {"actions": [{"action_id": action_id, "value": str(incident_id)}]}
    return urlencode({"payload": json.dumps(payload)}).encode("utf-8")


def _slack_sig(body: bytes, timestamp: str, secret: str) -> str:
    base = f"v0:{timestamp}:{body.decode('utf-8')}".encode("utf-8")
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
async def test_slack_actions_valid_signature_approves(client, clean_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", "shhh")
    incident_id = await _seed_pending()
    body = _slack_post_body(incident_id, APPROVE_ACTION_ID)
    ts = str(int(time.time()))
    resp = await client.post(
        "/webhooks/slack/actions",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": _slack_sig(body, ts, "shhh"),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert resp.status_code == 200

    async with AsyncSessionLocal() as s:
        run = (await s.execute(select(AgentRun).where(AgentRun.incident_id == incident_id))).scalar_one()
        incident = await s.get(Incident, incident_id)
    assert run.human_decision == "approved" and incident.status == "approved"


@pytest.mark.asyncio
async def test_slack_actions_bad_signature_rejected(client, clean_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_SIGNING_SECRET", "shhh")
    incident_id = await _seed_pending()
    body = _slack_post_body(incident_id, APPROVE_ACTION_ID)
    resp = await client.post(
        "/webhooks/slack/actions",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=deadbeef",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert resp.status_code == 401

    async with AsyncSessionLocal() as s:
        run = (await s.execute(select(AgentRun).where(AgentRun.incident_id == incident_id))).scalar_one()
    assert run.human_decision == "pending"  # unchanged
