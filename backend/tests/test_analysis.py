"""Analysis node + full analysis→action pipeline (LLMs mocked, retrieval real)."""

import hashlib
import hmac
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.agents import action, analysis, triage
from backend.agents.analysis import analysis_node
from backend.agents.triage import TriageClassification
from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.models.agent_run import AgentRun
from backend.rag import ingest

FIXTURES = Path(__file__).parent / "fixtures"


class _Msg:
    def __init__(self, content: str):
        self.content = content


class _FakeChat:
    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return _Msg(self._content)


class _FakeStructured:
    def __init__(self, c: TriageClassification):
        self._c = c

    async def ainvoke(self, messages, config=None):  # noqa: ANN001
        return self._c


def _state(**over) -> dict:
    base = {
        "event_id": str(uuid.uuid4()), "incident_id": str(uuid.uuid4()),
        "event_payload": {
            "title": "Check run 'integration-tests': failure",
            "body_text": "payments-gateway ConnectionTimeout after payment SDK 4.2.0 bump",
            "source": "github", "event_type": "check_run", "repo": "acme/checkout-service",
        },
        "severity": "P0", "confidence": 0.9, "retrieved_context": [],
        "root_cause": "", "suggested_action": "", "eval_scores": {}, "error": None,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_analysis_node_retrieves_and_reasons(monkeypatch, clean_db) -> None:
    await ingest.seed()  # historical incidents → document_chunks
    monkeypatch.setattr(analysis, "_get_llm", lambda: _FakeChat("Root cause: payment SDK timeout."))

    out = await analysis_node(_state())

    assert out.get("error") is None
    assert out["retrieved_context"], "expected non-empty retrieved context"
    assert isinstance(out["root_cause"], str) and out["root_cause"]


def _sig(body: bytes) -> str:
    return "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()


@pytest.mark.asyncio
async def test_full_pipeline_to_pending_proposal(monkeypatch, client, clean_db) -> None:
    await ingest.seed()
    # P0 → routes to analysis → action.
    monkeypatch.setattr(
        triage, "_get_structured_llm",
        lambda: _FakeStructured(TriageClassification(severity="P0", confidence=0.95)),
    )
    monkeypatch.setattr(analysis, "_get_llm", lambda: _FakeChat("Root cause: SDK timeout bump."))
    monkeypatch.setattr(action, "_get_llm", lambda: _FakeChat("Pin the SDK and set timeout=60."))

    body = (FIXTURES / "github_ci_failure.json").read_bytes()
    resp = await client.post(
        "/webhooks/github",
        content=body,
        headers={"X-Hub-Signature-256": _sig(body), "X-GitHub-Event": "check_run"},
    )
    assert resp.status_code == 200

    async with AsyncSessionLocal() as s:
        run = (await s.execute(select(AgentRun))).scalar_one()
    assert run.analysis_output is not None
    assert run.analysis_output["root_cause"] == "Root cause: SDK timeout bump."
    assert run.action_proposed == {"suggested_action": "Pin the SDK and set timeout=60."}
    assert run.human_decision == "pending"
