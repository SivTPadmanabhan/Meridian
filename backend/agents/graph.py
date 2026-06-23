"""Graph topology + routing.

triage → route_after_triage → {analysis | END}; analysis → action → eval → END.
"""

import asyncio

from langgraph.graph import START, END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import MemorySaver

from backend.config import settings
from backend.agents.state import MeridianState
from backend.agents.triage import triage_node
from backend.agents.analysis import analysis_node
from backend.agents.action import action_node
from backend.agents.eval_agent import eval_node

_graph: CompiledStateGraph | None = None

# Bounds concurrent graph runs (Phase 8 outbound throttle). Each run's LLM calls
# are sequential, so capping runs caps in-flight provider calls. Keyed per event
# loop so the suite's loop-per-test fixtures don't reuse a loop-bound primitive.
_semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _get_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)
        _semaphores[loop] = sem
    return sem


def route_after_triage(state: MeridianState) -> str:
    """AD-3: P0/P1 or low-confidence → analysis; errors and confident P2/P3 → END."""
    if state["error"] is not None:
        return END
    if state["severity"] in ("P0", "P1"):
        return "analysis"
    if state["confidence"] < settings.TRIAGE_CONFIDENCE_ESCALATION:
        return "analysis"
    return END


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(MeridianState)
    graph.add_node("triage", triage_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("action", action_node)
    graph.add_node("eval", eval_node)
    graph.add_edge(START, "triage")
    graph.add_conditional_edges(
        "triage", route_after_triage, {"analysis": "analysis", END: END}
    )
    graph.add_edge("analysis", "action")
    graph.add_edge("action", "eval")
    graph.add_edge("eval", END)
    return graph.compile(checkpointer=MemorySaver())


def get_graph() -> CompiledStateGraph:
    """Return the process-wide compiled graph, building it once on first use."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


async def ainvoke_graph(initial_state: dict, incident_id: str) -> dict:
    """Run the graph under the concurrency semaphore (AD: throttle at invocation).

    All graph runs go through here so ``LLM_MAX_CONCURRENCY`` caps simultaneous
    runs — and therefore in-flight provider calls — regardless of webhook burst.
    """
    async with _get_semaphore():
        return await get_graph().ainvoke(
            initial_state, config={"configurable": {"thread_id": incident_id}}
        )
