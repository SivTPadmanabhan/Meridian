"""Phase 7 — Notion runbook sync into the RAG store (source='notion')."""

import pytest

from backend.config import settings
from backend.integrations import notion
from backend.rag import ingest as ingest_mod


def test_plain_text_from_blocks_concatenates_rich_text() -> None:
    blocks = [
        {
            "type": "heading_1",
            "heading_1": {"rich_text": [{"plain_text": "Deploy rollback runbook"}]},
        },
        {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "Step 1: "}, {"plain_text": "revert the release."}]},
        },
        {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"plain_text": "Notify on-call"}]},
        },
        {"type": "unsupported", "unsupported": {}},  # no rich_text → skipped
    ]
    text = notion._plain_text_from_blocks(blocks)
    assert "Deploy rollback runbook" in text
    assert "Step 1: revert the release." in text
    assert "Notify on-call" in text


def test_page_title_extracts_from_title_property() -> None:
    page = {
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Incident response"}]},
        }
    }
    assert notion._page_title(page) == "Incident response"


@pytest.mark.asyncio
async def test_sync_runbooks_embeds_with_notion_source(monkeypatch) -> None:
    monkeypatch.setattr(settings, "NOTION_API_KEY", "secret_test")
    monkeypatch.setattr(settings, "NOTION_DATABASE_ID", "db123")

    async def _fake_fetch() -> list[tuple[str, str]]:
        return [
            ("Deploy rollback", "revert the release and redeploy"),
            ("Pager escalation", "page the secondary on-call after 10m"),
        ]

    monkeypatch.setattr(notion, "fetch_runbook_pages", _fake_fetch)

    recorded: list[tuple[str, str]] = []

    async def _fake_ingest_text(source: str, text: str) -> int:
        recorded.append((source, text))
        return 2

    monkeypatch.setattr(ingest_mod, "ingest_text", _fake_ingest_text)

    count = await notion.sync_runbooks()

    assert count == 4  # 2 pages × 2 chunks each
    assert {s for s, _ in recorded} == {"notion"}
    assert any("Deploy rollback" in t for _, t in recorded)


@pytest.mark.asyncio
async def test_sync_runbooks_no_key_is_noop(monkeypatch) -> None:
    monkeypatch.setattr(settings, "NOTION_API_KEY", "")
    monkeypatch.setattr(settings, "NOTION_DATABASE_ID", "")
    assert await notion.sync_runbooks() == 0
