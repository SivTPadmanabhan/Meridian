"""Phase 7 — Salesforce RevOps webhook receiver + OAuth refresh."""

import uuid

import pytest
from sqlalchemy import select

from backend.agents import triage
from backend.agents.triage import TriageClassification
from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.integrations import salesforce
from backend.integrations.salesforce import parse_salesforce_event, refresh_access_token
from backend.models.event import Event


# --------------------------------------------------------------------------- #
# parse_salesforce_event → NormalizedEvent(source='salesforce')
# --------------------------------------------------------------------------- #
def test_parse_salesforce_event_populates_all_fields() -> None:
    payload = {
        "sobject": "Case",
        "event_type": "case.escalated",
        "account": "Acme Corp",
        "subject": "Enterprise renewal at risk",
        "description": "Champion left; renewal in 30 days unconfirmed.",
        "created_date": "2026-06-10T14:00:00Z",
    }
    ev = parse_salesforce_event(payload)

    assert ev.source == "salesforce"
    assert ev.event_type == "case.escalated"
    assert ev.repo == "Acme Corp"
    assert "Enterprise renewal at risk" in ev.title
    assert "Champion left" in ev.body_text
    assert ev.occurred_at.year == 2026
    assert ev.raw == payload


def test_parse_salesforce_event_defensive_on_sparse_payload() -> None:
    ev = parse_salesforce_event({})
    assert ev.source == "salesforce"
    assert ev.event_type  # non-empty fallback
    assert ev.title


# --------------------------------------------------------------------------- #
# OAuth refresh
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, data: dict) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._data


class _FakeAsyncClient:
    def __init__(self, data: dict) -> None:
        self._data = data
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):  # noqa: ANN002
        return False

    async def post(self, url, data=None, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"url": url, "data": data})
        return _FakeResponse(self._data)


@pytest.mark.asyncio
async def test_refresh_access_token_returns_token(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SALESFORCE_CLIENT_ID", "cid")
    monkeypatch.setattr(settings, "SALESFORCE_CLIENT_SECRET", "secret")
    monkeypatch.setattr(settings, "SALESFORCE_REFRESH_TOKEN", "rtok")
    fake = _FakeAsyncClient({"access_token": "00Dxx!new", "instance_url": "https://x"})
    monkeypatch.setattr(salesforce.httpx, "AsyncClient", lambda *a, **k: fake)

    token = await refresh_access_token()

    assert token == "00Dxx!new"
    assert fake.calls[0]["data"]["grant_type"] == "refresh_token"


@pytest.mark.asyncio
async def test_refresh_access_token_no_creds_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SALESFORCE_CLIENT_ID", "")
    monkeypatch.setattr(settings, "SALESFORCE_REFRESH_TOKEN", "")
    assert await refresh_access_token() is None


# --------------------------------------------------------------------------- #
# POST /webhooks/salesforce — token gate + store (AD-7)
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _mock_triage_llm(monkeypatch):
    monkeypatch.setattr(
        triage, "_get_structured_llm",
        lambda: _FakeTriage(),
    )


class _FakeTriage:
    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return TriageClassification(severity="P2", confidence=0.9, category="RevOps")


@pytest.mark.asyncio
async def test_salesforce_webhook_good_token_stores_event(client, clean_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SALESFORCE_WEBHOOK_SECRET", "sf-secret")
    payload = {"sobject": "Opportunity", "event_type": "opp.stalled", "subject": "Deal stuck"}
    resp = await client.post(
        "/webhooks/salesforce",
        json=payload,
        headers={"X-Salesforce-Token": "sf-secret"},
    )
    assert resp.status_code == 200

    async with AsyncSessionLocal() as s:
        events = (await s.execute(select(Event).where(Event.source == "salesforce"))).scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "opp.stalled"


@pytest.mark.asyncio
async def test_salesforce_webhook_bad_token_rejected(client, clean_db, monkeypatch) -> None:
    monkeypatch.setattr(settings, "SALESFORCE_WEBHOOK_SECRET", "sf-secret")
    resp = await client.post(
        "/webhooks/salesforce",
        json={"sobject": "Case"},
        headers={"X-Salesforce-Token": "wrong"},
    )
    assert resp.status_code == 401

    async with AsyncSessionLocal() as s:
        count = len((await s.execute(select(Event))).scalars().all())
    assert count == 0
