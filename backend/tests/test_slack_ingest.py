"""Phase 7 — Slack channel-history ingestion into the RAG store (source='slack')."""

import pytest

from backend.config import settings
from backend.integrations import slack
from backend.rag import ingest as ingest_mod


class _FakeHistoryClient:
    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages
        self.calls: list[dict] = []

    async def conversations_history(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return {"messages": self._messages, "ok": True}


@pytest.mark.asyncio
async def test_fetch_channel_history_returns_message_texts(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(settings, "SLACK_INGEST_CHANNEL_ID", "C-INGEST")
    fake = _FakeHistoryClient(
        [
            {"text": "Payments API returned 500s after the 14:00 deploy"},
            {"text": "Rolled back; root cause was a bad migration"},
            {"subtype": "channel_join", "text": ""},  # noise — no usable text
        ]
    )
    monkeypatch.setattr(slack, "_get_client", lambda: fake)

    texts = await slack.fetch_channel_history()

    assert texts == [
        "Payments API returned 500s after the 14:00 deploy",
        "Rolled back; root cause was a bad migration",
    ]
    assert fake.calls[0]["channel"] == "C-INGEST"


@pytest.mark.asyncio
async def test_ingest_slack_history_embeds_with_slack_source(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(settings, "SLACK_INGEST_CHANNEL_ID", "C-INGEST")
    fake = _FakeHistoryClient([{"text": "incident A"}, {"text": "incident B"}])
    monkeypatch.setattr(slack, "_get_client", lambda: fake)

    recorded: list[tuple[str, str]] = []

    async def _fake_ingest_text(source: str, text: str) -> int:
        recorded.append((source, text))
        return 1

    monkeypatch.setattr(ingest_mod, "ingest_text", _fake_ingest_text)

    count = await slack.ingest_slack_history()

    assert count == 1
    assert len(recorded) == 1
    source, text = recorded[0]
    assert source == "slack"
    assert "incident A" in text and "incident B" in text


@pytest.mark.asyncio
async def test_ingest_slack_history_no_creds_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(settings, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "SLACK_INGEST_CHANNEL_ID", "")

    def _boom():
        raise AssertionError("client must not be built without credentials")

    monkeypatch.setattr(slack, "_get_client", _boom)

    assert await slack.ingest_slack_history() == 0
