"""Phase 8 — Cassandra audit log (driver mocked; no live cluster needed).

These tests assert the DDL shape and that inserts are parameterized (bound
params only — never string-interpolated raw bodies). They never touch a real
cluster: ``_get_session`` is monkeypatched to a MagicMock.
"""

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select

import backend.routes.webhooks as webhooks
from backend.agents import triage
from backend.agents.triage import TriageClassification
from backend.config import settings
from backend.db import cassandra as audit
from backend.db.session import AsyncSessionLocal
from backend.models.event import Event
from backend.models.incident import Incident

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeTriageLLM:
    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return TriageClassification(severity="P2", confidence=0.9)


@pytest.fixture
def _mock_triage(monkeypatch) -> None:
    monkeypatch.setattr(triage, "_get_structured_llm", lambda: _FakeTriageLLM())


def _gh_sig(body: bytes) -> str:
    return "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()


async def _count(model) -> int:  # noqa: ANN001
    async with AsyncSessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model))


@pytest.mark.asyncio
async def test_init_audit_log_creates_keyspace_and_table(monkeypatch) -> None:
    session = MagicMock()
    monkeypatch.setattr(audit, "_get_session", lambda: session)

    await audit.init_audit_log()

    executed = [call.args[0] for call in session.execute.call_args_list]
    assert any(
        "CREATE KEYSPACE" in q and settings.CASSANDRA_KEYSPACE in q for q in executed
    )
    assert any(
        "events_by_day" in q and "PRIMARY KEY" in q for q in executed
    )


@pytest.mark.asyncio
async def test_append_event_inserts_with_bound_params(monkeypatch) -> None:
    session = MagicMock()
    monkeypatch.setattr(audit, "_get_session", lambda: session)

    eid = uuid.uuid4()
    ts = datetime(2026, 6, 11, 14, 30, tzinfo=timezone.utc)
    await audit.append_event(
        event_id=eid,
        source="github",
        event_type="check_run",
        raw_body='{"secret": "do-not-interpolate"}',  # pragma: allowlist secret
        received_at=ts,
    )

    session.execute.assert_called_once()
    query, params = session.execute.call_args.args
    assert "INSERT INTO" in query and "events_by_day" in query
    # Bound params only — the raw body must never appear in the query string.
    assert "%s" in query
    assert "do-not-interpolate" not in query
    # day partition derived from received_at; full row passed as a params tuple.
    assert params == (ts.date(), ts, eid, "github", "check_run",
                      '{"secret": "do-not-interpolate"}')  # pragma: allowlist secret


@pytest.mark.asyncio
async def test_append_event_offloads_to_thread(monkeypatch) -> None:
    """The sync driver call must run via asyncio.to_thread, not block the loop."""
    session = MagicMock()
    monkeypatch.setattr(audit, "_get_session", lambda: session)

    calls: list[str] = []
    real_to_thread = audit.asyncio.to_thread

    async def _spy(fn, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        calls.append(fn.__name__)
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(audit.asyncio, "to_thread", _spy)

    await audit.append_event(
        event_id=uuid.uuid4(), source="gitlab", event_type="pipeline",
        raw_body="{}", received_at=datetime.now(timezone.utc),
    )
    assert calls == ["_append_sync"]


# --------------------------------------------------------------------------- #
# Webhook background task → audit append (gated by CASSANDRA_AUDIT_ENABLED)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_webhook_appends_raw_event_when_enabled(
    client, clean_db, monkeypatch, _mock_triage
) -> None:
    monkeypatch.setattr(settings, "CASSANDRA_AUDIT_ENABLED", True)
    appended: list[dict] = []

    async def _fake_append(**kwargs) -> None:  # noqa: ANN003
        appended.append(kwargs)

    monkeypatch.setattr(webhooks, "audit_append_event", _fake_append)

    body = (FIXTURES / "github_ci_failure.json").read_bytes()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _gh_sig(body), "X-GitHub-Event": "check_run"},
    )
    assert resp.status_code == 200

    assert len(appended) == 1
    row = appended[0]
    assert row["source"] == "github"
    assert row["event_type"] == "check_run"
    assert row["raw_body"] == body.decode("utf-8")
    assert isinstance(row["event_id"], uuid.UUID)
    assert isinstance(row["received_at"], datetime)


@pytest.mark.asyncio
async def test_webhook_skips_audit_when_disabled(
    client, clean_db, monkeypatch, _mock_triage
) -> None:
    monkeypatch.setattr(settings, "CASSANDRA_AUDIT_ENABLED", False)
    appended: list[dict] = []

    async def _fake_append(**kwargs) -> None:  # noqa: ANN003
        appended.append(kwargs)

    monkeypatch.setattr(webhooks, "audit_append_event", _fake_append)

    body = (FIXTURES / "github_ci_failure.json").read_bytes()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _gh_sig(body), "X-GitHub-Event": "check_run"},
    )
    assert resp.status_code == 200
    assert appended == []


@pytest.mark.asyncio
async def test_audit_failure_never_blocks_pipeline(
    client, clean_db, monkeypatch, _mock_triage
) -> None:
    monkeypatch.setattr(settings, "CASSANDRA_AUDIT_ENABLED", True)

    async def _boom(**kwargs) -> None:  # noqa: ANN003
        raise RuntimeError("cassandra unreachable")

    monkeypatch.setattr(webhooks, "audit_append_event", _boom)

    body = (FIXTURES / "github_ci_failure.json").read_bytes()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _gh_sig(body), "X-GitHub-Event": "check_run"},
    )
    # The Postgres pipeline still completes despite the audit failure.
    assert resp.status_code == 200
    assert await _count(Event) == 1
    assert await _count(Incident) == 1
