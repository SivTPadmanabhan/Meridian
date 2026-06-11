"""Action node — propose one concrete remediation (Claude Sonnet, AD-6).

Persists the analysis + proposal to the AgentRun and marks it
``human_decision='pending'`` (AD-1: approval is a DB concern, resolved later by
the Slack webhook or POST /incidents/{id}/approve). The graph never pauses.
"""

import logging
import uuid

from langchain_anthropic import ChatAnthropic
from langfuse.langchain import CallbackHandler
from sqlalchemy import select

from backend.config import settings
from backend.agents.state import MeridianState
from backend.db.session import AsyncSessionLocal
from backend.integrations.slack import build_alert_message, send_alert
from backend.models.agent_run import AgentRun
from backend.models.incident import Incident

logger = logging.getLogger(__name__)

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


_SYSTEM_PROMPT = (
    "You are the action stage of an engineering operations assistant. "
    "Given a root-cause analysis, propose exactly ONE concrete remediation step "
    "an on-call engineer can take now. One or two sentences, imperative voice."
)


async def _propose(state: MeridianState) -> str:
    handler = CallbackHandler()
    user = (
        f"Event:\n{state['event_payload'].get('title', '')}\n\n"
        f"Root cause:\n{state['root_cause']}\n\n"
        "Propose one concrete remediation step."
    )
    result = await _get_llm().ainvoke(
        [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user}],
        config={
            "callbacks": [handler],
            "run_name": "action.propose",
            "metadata": {
                "event_id": state["event_id"],
                "incident_id": state["incident_id"],
            },
        },
    )
    content = result.content
    return content if isinstance(content, str) else str(content)


async def _persist(incident_id: str, analysis_output: dict, action_proposed: dict) -> None:
    async with AsyncSessionLocal() as session:
        run = (
            await session.execute(
                select(AgentRun).where(AgentRun.incident_id == uuid.UUID(incident_id))
            )
        ).scalar_one()
        run.analysis_output = analysis_output
        run.action_proposed = action_proposed
        run.human_decision = "pending"
        await session.commit()


async def _notify(incident_id: str) -> None:
    """Send the Slack alert for a stored proposal (Phase 5).

    Guarded by its own try/except: a Slack failure must not fail the run or skip
    the downstream eval node. Degrades to a no-op when Slack is unconfigured.
    """
    try:
        async with AsyncSessionLocal() as session:
            run = (
                await session.execute(
                    select(AgentRun).where(AgentRun.incident_id == uuid.UUID(incident_id))
                )
            ).scalar_one()
            incident = await session.get(Incident, uuid.UUID(incident_id))
        await send_alert(build_alert_message(run, incident))
    except Exception:
        logger.exception("Slack alert failed for incident %s", incident_id)


async def action_node(state: MeridianState) -> dict:
    try:
        suggested_action = await _propose(state)
        analysis_output = {
            "root_cause": state["root_cause"],
            "retrieved_context": state["retrieved_context"],
        }
        action_proposed = {"suggested_action": suggested_action}
        await _persist(state["incident_id"], analysis_output, action_proposed)

        # Phase 5: alert after the proposal is stored, before eval (AD-1).
        await _notify(state["incident_id"])

        return {"suggested_action": suggested_action}
    except Exception as exc:
        logger.exception("action_node failed for incident %s", state["incident_id"])
        return {"error": f"action: {exc}", "suggested_action": ""}
