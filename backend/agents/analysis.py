"""Analysis node — RAG retrieval + root-cause reasoning (Claude Sonnet, AD-6).

Retrieves similar past incidents (traced as the ``analysis.retrieve`` span),
applies the 3,000-token context guard, then reasons about the root cause
(``analysis.reason`` LLM call). Any failure is caught at the node boundary.
"""

import logging

from langchain_anthropic import ChatAnthropic
from langfuse import observe
from langfuse.langchain import CallbackHandler

from backend.config import settings
from backend.agents.state import MeridianState
from backend.rag.retriever import retrieve

logger = logging.getLogger(__name__)

CONTEXT_TOKEN_BUDGET = 3000           # CLAUDE.md RAG context guard
_CHARS_PER_TOKEN = 4                  # rough token estimate: len(text) // 4

_llm = None


def _get_llm() -> ChatAnthropic:
    global _llm
    if _llm is None:
        _llm = ChatAnthropic(
            model=settings.ANTHROPIC_ANALYSIS_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            max_tokens=1024,
        )
    return _llm


def _event_summary(payload: dict) -> str:
    return f"{payload.get('title', '')}\n\n{payload.get('body_text', '')}".strip()


def _truncate_context(chunks: list[str]) -> list[str]:
    """Keep the top chunks whose combined size fits the token budget."""
    budget_chars = CONTEXT_TOKEN_BUDGET * _CHARS_PER_TOKEN
    kept: list[str] = []
    total = 0
    for chunk in chunks:
        if kept and total + len(chunk) > budget_chars:
            break
        kept.append(chunk)
        total += len(chunk)
    return kept


@observe(name="analysis.retrieve", as_type="retriever")
async def _retrieve(query: str) -> list[str]:
    return await retrieve(query, k=5)


_SYSTEM_PROMPT = (
    "You are the analysis stage of an engineering operations assistant. "
    "Given a DevOps event and similar past incidents, identify the most likely "
    "root cause in 2–3 sentences. Be concrete and reference the evidence."
)


async def _reason(state: MeridianState, contexts: list[str]) -> str:
    handler = CallbackHandler()
    context_block = "\n\n---\n\n".join(contexts) if contexts else "(no similar incidents found)"
    user = (
        f"Event:\n{_event_summary(state['event_payload'])}\n\n"
        f"Similar past incidents:\n{context_block}\n\n"
        "What is the root cause?"
    )
    result = await _get_llm().ainvoke(
        [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user}],
        config={
            "callbacks": [handler],
            "run_name": "analysis.reason",
            "metadata": {
                "event_id": state["event_id"],
                "incident_id": state["incident_id"],
            },
        },
    )
    content = result.content
    return content if isinstance(content, str) else str(content)


async def analysis_node(state: MeridianState) -> dict:
    try:
        query = _event_summary(state["event_payload"])
        contexts = await _retrieve(query)
        guarded = _truncate_context(contexts)
        root_cause = await _reason(state, guarded)
        return {"retrieved_context": contexts, "root_cause": root_cause}
    except Exception as exc:
        logger.exception("analysis_node failed for incident %s", state["incident_id"])
        return {"error": f"analysis: {exc}", "root_cause": "", "retrieved_context": []}
