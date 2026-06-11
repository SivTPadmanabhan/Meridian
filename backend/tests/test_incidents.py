"""Background pipeline + /incidents endpoint (triage LLM mocked)."""

import hashlib
import hmac
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.agents import triage
from backend.agents.triage import TriageClassification
from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.models.agent_run import AgentRun
from backend.models.eval_result import EvalResult
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
    # P2/P3 runs end at triage with no proposal → no pending decision.
    assert data[0]["human_decision"] is None


# --------------------------------------------------------------------------- #
# GET /incidents/{id} — full trace + online eval scores
# --------------------------------------------------------------------------- #
async def _seed_full_incident() -> uuid.UUID:
    """A P0 incident with a completed AgentRun and one online eval row."""
    async with AsyncSessionLocal() as s:
        event = Event(
            id=uuid.uuid4(), source="github", event_type="check_run",
            payload={}, raw_body="{}",
        )
        s.add(event)
        await s.flush()
        incident = Incident(
            id=uuid.uuid4(), event_id=event.id, severity="P0", status="open",
            title="Check run 'integration-tests': failure",
        )
        s.add(incident)
        await s.flush()
        run = AgentRun(
            id=uuid.uuid4(), incident_id=incident.id,
            triage_output={"severity": "P0", "confidence": 0.91},
            analysis_output={"root_cause": "payment SDK timeout",
                             "retrieved_context": ["past A", "past B"]},
            action_proposed={"suggested_action": "Pin the SDK and set timeout=60."},
            human_decision="pending",
        )
        s.add(run)
        await s.flush()
        s.add(EvalResult(
            id=uuid.uuid4(), eval_type="online", agent_run_id=run.id,
            faithfulness=0.92, response_relevancy=0.88, hallucination_rate=0.08,
            judge_model="gpt-5.4-mini",
        ))
        await s.commit()
        return incident.id


@pytest.mark.asyncio
async def test_incident_detail_returns_full_trace(client, clean_db) -> None:
    incident_id = await _seed_full_incident()

    resp = await client.get(f"/incidents/{incident_id}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["id"] == str(incident_id)
    assert data["severity"] == "P0"
    assert data["status"] == "open"
    assert data["human_decision"] == "pending"
    assert data["triage_output"]["confidence"] == 0.91
    assert data["analysis_output"]["root_cause"] == "payment SDK timeout"
    assert data["analysis_output"]["retrieved_context"] == ["past A", "past B"]
    assert data["action_proposed"]["suggested_action"].startswith("Pin the SDK")

    assert len(data["eval_scores"]) == 1
    score = data["eval_scores"][0]
    assert score["eval_type"] == "online"
    assert score["faithfulness"] == 0.92
    assert score["hallucination_rate"] == 0.08
    assert score["judge_model"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_incident_detail_unknown_returns_404(client, clean_db) -> None:
    resp = await client.get(f"/incidents/{uuid.uuid4()}")
    assert resp.status_code == 404
