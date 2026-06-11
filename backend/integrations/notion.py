"""Notion runbook sync (V2).

Fetches pages from a configured Notion database, extracts their plain text, and
embeds them into ``document_chunks`` with ``source='notion'`` so runbooks become
retrievable RAG context. Uses the Notion REST API directly via httpx (no SDK —
consistent with the hand-rolled RAG layer, AD-5).

Degrades to a no-op when ``NOTION_API_KEY`` / ``NOTION_DATABASE_ID`` are unset,
matching the no-keys policy used across the integrations.
"""

import logging

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

_API_ROOT = "https://api.notion.com/v1"
# Notion block types whose rich_text we treat as runbook prose.
_TEXT_BLOCK_TYPES = (
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "quote",
    "callout",
    "to_do",
    "toggle",
)


def _notion_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.NOTION_API_KEY}",
        "Notion-Version": settings.NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _plain_text_from_blocks(blocks: list[dict]) -> str:
    """Concatenate the plain_text of every supported block into newline-joined prose."""
    lines: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype not in _TEXT_BLOCK_TYPES:
            continue
        rich = (block.get(btype) or {}).get("rich_text") or []
        text = "".join(part.get("plain_text", "") for part in rich).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _page_title(page: dict) -> str:
    """Extract the title from a page's title-typed property (name varies by DB)."""
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            title_parts = prop.get("title") or []
            return "".join(part.get("plain_text", "") for part in title_parts).strip()
    return "Untitled"


async def _fetch_block_text(client: httpx.AsyncClient, page_id: str) -> str:
    resp = await client.get(
        f"{_API_ROOT}/blocks/{page_id}/children",
        headers=_notion_headers(),
        params={"page_size": 100},
    )
    resp.raise_for_status()
    return _plain_text_from_blocks(resp.json().get("results") or [])


async def fetch_runbook_pages() -> list[tuple[str, str]]:
    """Return ``(title, text)`` for each page in the configured Notion database."""
    if not settings.NOTION_API_KEY or not settings.NOTION_DATABASE_ID:
        logger.warning("Notion not configured (key/database empty); skipping")
        return []
    pages: list[tuple[str, str]] = []
    async with httpx.AsyncClient(timeout=15.0) as client:
        query = await client.post(
            f"{_API_ROOT}/databases/{settings.NOTION_DATABASE_ID}/query",
            headers=_notion_headers(),
        )
        query.raise_for_status()
        for page in query.json().get("results") or []:
            title = _page_title(page)
            text = await _fetch_block_text(client, page["id"])
            if text:
                pages.append((title, text))
    return pages


async def sync_runbooks() -> int:
    """Embed every Notion runbook page into ``document_chunks``. Returns chunks stored."""
    pages = await fetch_runbook_pages()
    if not pages:
        return 0
    from backend.rag import ingest

    total = 0
    for title, text in pages:
        total += await ingest.ingest_text("notion", f"{title}\n\n{text}")
    logger.info("notion sync embedded %d chunk(s) from %d page(s)", total, len(pages))
    return total
