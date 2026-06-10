"""Online eval node: stored EvalResult on success; never crashes on judge failure."""

import uuid

import pandas as pd
import pytest
from sqlalchemy import func, select

from backend.agents import eval_agent
from backend.agents.eval_agent import eval_node
from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.models.agent_run import AgentRun
from backend.models.eval_result import EvalResult
from backend.models.event import Event
from backend.models.incident import Incident


class _FakeResult:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def to_pandas(self) -> pd.DataFrame:
        return self._df


async def _make_run() -> str:
    """Create an Event → Incident → AgentRun chain; return the incident_id (str)."""
    async with AsyncSessionLocal() as s:
        event = Event(id=uuid.uuid4(), source="github", event_type="check_run",
                      payload={}, raw_body="{}")
        s.add(event)
        await s.flush()
        incident = Incident(id=uuid.uuid4(), event_id=event.id, status="open", title="t")
        s.add(incident)
        await s.flush()
        s.add(AgentRun(id=uuid.uuid4(), incident_id=incident.id))
        await s.commit()
        return str(incident.id)


def _state(incident_id: str) -> dict:
    return {
        "event_id": str(uuid.uuid4()), "incident_id": incident_id,
        "event_payload": {"title": "CI failure", "body_text": "timeout"},
        "severity": "P0", "confidence": 0.9,
        "retrieved_context": ["past incident about timeouts"],
        "root_cause": "payment SDK timeout", "suggested_action": "pin SDK",
        "eval_scores": {}, "error": None,
    }


@pytest.fixture(autouse=True)
def _dummy_openai_key(monkeypatch):
    # Allow the judge (ChatOpenAI) to construct; evaluate itself is stubbed below.
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")


@pytest.mark.asyncio
async def test_eval_node_stores_online_result(monkeypatch, clean_db) -> None:
    incident_id = await _make_run()
    df = pd.DataFrame([{"faithfulness": 0.9, "answer_relevancy": 0.8}])
    monkeypatch.setattr(eval_agent, "evaluate", lambda **kw: _FakeResult(df))

    out = await eval_node(_state(incident_id))

    assert 0.0 <= out["eval_scores"]["faithfulness"] <= 1.0
    assert 0.0 <= out["eval_scores"]["response_relevancy"] <= 1.0
    assert out["eval_scores"]["hallucination_rate"] == pytest.approx(0.1)

    async with AsyncSessionLocal() as s:
        result = (await s.execute(select(EvalResult))).scalar_one()
    assert result.eval_type == "online"
    assert result.agent_run_id is not None
    assert result.faithfulness == 0.9
    assert result.hallucination_rate == pytest.approx(0.1)
    assert result.judge_model == settings.OPENAI_JUDGE_MODEL


@pytest.mark.asyncio
async def test_eval_node_swallows_judge_failure(monkeypatch, clean_db) -> None:
    incident_id = await _make_run()

    def _boom(**kw):
        raise RuntimeError("judge exploded")

    monkeypatch.setattr(eval_agent, "evaluate", _boom)

    out = await eval_node(_state(incident_id))  # must not raise

    assert out == {"eval_scores": {}}
    async with AsyncSessionLocal() as s:
        count = await s.scalar(select(func.count()).select_from(EvalResult))
    assert count == 0
