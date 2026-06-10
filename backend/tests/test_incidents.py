"""Background pipeline + /incidents endpoint (triage LLM mocked)."""

import hashlib
import hmac
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.agents import triage
from backend.agents.triage import TriageClassification
from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.models.agent_run import AgentRun
from backend.models.event import Event
from backend.models.incident import Incident

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeLLM:
    def __init__(self, c: TriageClassification):
        self._c = c

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return self._c


@pytest.fixture(autouse=True)
def _mock_triage_llm(monkeypatch):
    # Confident P2 → run ends at triage as triaged_low (AD-3).
    monkeypatch.setattr(
        triage, "_get_structured_llm",
        lambda: _FakeLLM(TriageClassification(severity="P2", confidence=0.9)),
    )


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()


async def _count(model) -> int:
    async with AsyncSessionLocal() as s:
        return await s.scalar(select(func.count()).select_from(model))


@pytest.mark.asyncio
async def test_pipeline_creates_one_row_each(client, clean_db) -> None:
    body = (FIXTURES / "github_ci_failure.json").read_bytes()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _sig(body), "X-GitHub-Event": "check_run"},
    )
    assert resp.status_code == 200

    assert await _count(Event) == 1
    assert await _count(Incident) == 1
    assert await _count(AgentRun) == 1

    async with AsyncSessionLocal() as s:
        run = (await s.execute(select(AgentRun))).scalar_one()
        incident = (await s.execute(select(Incident))).scalar_one()
    assert run.triage_output == {"severity": "P2", "confidence": 0.9}
    assert incident.severity == "P2"
    assert incident.status == "triaged_low"


@pytest.mark.asyncio
async def test_list_incidents_returns_summary(client, clean_db) -> None:
    body = (FIXTURES / "github_ci_failure.json").read_bytes()
    await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _sig(body), "X-GitHub-Event": "check_run"},
    )
    resp = await client.get("/incidents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["severity"] == "P2"
    assert data[0]["status"] == "triaged_low"
    assert data[0]["confidence"] == 0.9
