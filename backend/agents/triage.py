"""Triage node — classify severity + confidence with Claude Haiku.

Tiered model (AD-6): triage runs on the cheap high-volume model. The LLM is
constructed lazily so an empty/missing ANTHROPIC_API_KEY never breaks import; a
runtime failure is caught at the node boundary, which sets ``error`` and routes
the run to END (AD-3).
"""

import logging
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langfuse.langchain import CallbackHandler
from pydantic import BaseModel, Field

from backend.config import settings
from backend.agents.state import MeridianState

logger = logging.getLogger(__name__)

_structured_llm = None


class TriageClassification(BaseModel):
    """Structured triage output."""

    severity: Literal["P0", "P1", "P2", "P3"] = Field(
        description="P0 = critical/outage, P1 = high, P2 = medium, P3 = low/noise"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="confidence in the severity label, 0.0–1.0"
    )
    category: Literal["DevOps", "RevOps"] = Field(
        default="DevOps",
        description="DevOps for engineering signals (GitHub/GitLab); "
        "RevOps for Salesforce revenue-operations signals",
    )


def _get_structured_llm():
    global _structured_llm
    if _structured_llm is None:
        llm = ChatAnthropic(
            model=settings.ANTHROPIC_TRIAGE_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            max_tokens=1024,
            max_retries=settings.LLM_MAX_RETRIES,  # backoff on 429/5xx
        )
        _structured_llm = llm.with_structured_output(TriageClassification)
    return _structured_llm


_SYSTEM_PROMPT = (
    "You are the triage stage of an enterprise operations assistant. "
    "Classify the severity AND category of an incoming event. "
    "P0 = production outage / critical breakage; P1 = high impact, needs attention soon; "
    "P2 = medium, routine failure; P3 = low / informational / noise. "
    "Category: use 'DevOps' for engineering signals (GitHub/GitLab — CI failures, "
    "pushes, PRs, pipelines); use 'RevOps' for Salesforce revenue-operations signals "
    "(stalled opportunities, escalated cases, at-risk renewals). "
    "Return a severity, your confidence in it, and the category."
)


def _build_messages(payload: dict) -> list[dict]:
    title = payload.get("title", "")
    body = payload.get("body_text", "")
    source = payload.get("source", "")
    event_type = payload.get("event_type", "")
    repo = payload.get("repo", "")
    user = (
        f"Source: {source} / {event_type}\nRepo: {repo}\n"
        f"Title: {title}\n\nDetails:\n{body}"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


async def triage_node(state: MeridianState) -> dict:
    handler = CallbackHandler()
    try:
        result: TriageClassification = await _get_structured_llm().ainvoke(
            _build_messages(state["event_payload"]),
            config={
                "callbacks": [handler],
                "run_name": "triage.classify",
                "metadata": {
                    "event_id": state["event_id"],
                    "incident_id": state["incident_id"],
                },
            },
        )
        return {
            "severity": result.severity,
            "confidence": result.confidence,
            "category": result.category,
        }
    except Exception as exc:
        logger.exception("triage_node failed for event %s", state["event_id"])
        # error set → route_after_triage sends the run to END.
        return {"error": f"triage: {exc}", "severity": "P3", "confidence": 1.0, "category": "DevOps"}
