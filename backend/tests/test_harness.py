"""Offline harness: 5 pairs with a stubbed judge → 5 offline EvalResult rows."""

import pandas as pd
import pytest
from sqlalchemy import select

from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.eval import harness
from backend.models.eval_result import EvalResult

_PAIRS = [
    {"question": f"q{i}", "answer": f"a{i}", "contexts": [f"c{i}"], "incident_type": "ci_failure"}
    for i in range(5)
]


@pytest.fixture(autouse=True)
def _stub_judge_and_generation(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    async def _fake_generate(question: str):
        return f"response for {question}", ["retrieved ctx"]

    monkeypatch.setattr(harness, "generate_response", _fake_generate)

    df = pd.DataFrame([{
        "faithfulness": 0.8, "answer_relevancy": 0.7,
        "context_precision": 0.9, "factual_correctness": 0.6,
    }])
    monkeypatch.setattr(
        harness, "evaluate",
        lambda **kw: type("R", (), {"to_pandas": lambda self: df})(),
    )


@pytest.mark.asyncio
async def test_harness_stores_offline_rows(clean_db) -> None:
    stored = await harness.run(_PAIRS)
    assert len(stored) == 5

    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(EvalResult))).scalars().all()

    assert len(rows) == 5
    for r in rows:
        assert r.eval_type == "offline"
        assert r.agent_run_id is None
        for value in (
            r.faithfulness, r.response_relevancy,
            r.context_precision, r.factual_correctness, r.hallucination_rate,
        ):
            assert value is not None and 0.0 <= value <= 1.0
        assert r.hallucination_rate == pytest.approx(0.2)
        assert r.judge_model == settings.OPENAI_JUDGE_MODEL
