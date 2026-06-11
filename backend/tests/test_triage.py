"""Triage routing logic + node behavior (LLM mocked — hermetic, no API key)."""

import pytest

from backend.agents import triage
from backend.agents.graph import route_after_triage
from backend.agents.triage import TriageClassification, triage_node
from backend.config import settings


def _state(severity: str, confidence: float, error: str | None = None) -> dict:
    return {
        "event_id": "e", "incident_id": "i", "event_payload": {},
        "severity": severity, "confidence": confidence,
        "retrieved_context": [], "root_cause": "", "suggested_action": "",
        "eval_scores": {}, "error": error,
    }


@pytest.mark.parametrize(
    "severity,confidence,expected",
    [
        ("P0", 0.9, "analysis"),
        ("P1", 0.9, "analysis"),
        ("P2", 0.9, "__end__"),     # confident low severity → END
        ("P3", 0.95, "__end__"),
        ("P2", 0.3, "analysis"),    # low confidence escalates regardless (AD-3)
        ("P3", 0.1, "analysis"),
    ],
)
def test_route_after_triage(severity: str, confidence: float, expected: str) -> None:
    assert route_after_triage(_state(severity, confidence)) == expected


def test_route_after_triage_error_goes_to_end() -> None:
    assert route_after_triage(_state("P0", 0.9, error="boom")) == "__end__"


class _FakeLLM:
    def __init__(self, classification: TriageClassification):
        self._c = classification

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return self._c


@pytest.mark.parametrize("severity", ["P0", "P1", "P2", "P3"])
@pytest.mark.asyncio
async def test_triage_node_returns_valid_shape(monkeypatch, severity: str) -> None:
    monkeypatch.setattr(
        triage, "_get_structured_llm",
        lambda: _FakeLLM(TriageClassification(severity=severity, confidence=0.8)),
    )
    out = await triage_node(_state("", 0.0))
    assert out["severity"] in ("P0", "P1", "P2", "P3")
    assert 0.0 <= out["confidence"] <= 1.0
    assert out["severity"] == severity
    # P0/P1 must route onward to analysis.
    merged = _state(out["severity"], out["confidence"], out.get("error"))
    if severity in ("P0", "P1"):
        assert route_after_triage(merged) == "analysis"


def test_classification_defaults_category_to_devops() -> None:
    # Existing call sites construct without a category — must keep working.
    assert TriageClassification(severity="P0", confidence=0.9).category == "DevOps"


@pytest.mark.asyncio
async def test_triage_node_carries_revops_category(monkeypatch) -> None:
    monkeypatch.setattr(
        triage, "_get_structured_llm",
        lambda: _FakeLLM(
            TriageClassification(severity="P2", confidence=0.8, category="RevOps")
        ),
    )
    out = await triage_node(_state("", 0.0))
    assert out["category"] == "RevOps"


def test_triage_prompt_is_source_aware() -> None:
    # Salesforce events should steer the model toward RevOps; DevOps for git hosts.
    salesforce = triage._build_messages({"source": "salesforce", "title": "Deal stalled"})
    github = triage._build_messages({"source": "github", "title": "CI failed"})
    assert "RevOps" in salesforce[0]["content"]
    assert "salesforce" in salesforce[1]["content"].lower()
    assert "RevOps" in github[0]["content"]  # categories explained in the system prompt


@pytest.mark.asyncio
async def test_triage_node_error_path_sets_error_and_ends(monkeypatch) -> None:
    class _Boom:
        async def ainvoke(self, *a, **k):
            raise RuntimeError("anthropic down")

    monkeypatch.setattr(triage, "_get_structured_llm", lambda: _Boom())
    out = await triage_node(_state("", 0.0))
    assert out["error"] is not None
    assert out["severity"] == "P3" and out["confidence"] == 1.0
    assert route_after_triage(_state(out["severity"], out["confidence"], out["error"])) == "__end__"
