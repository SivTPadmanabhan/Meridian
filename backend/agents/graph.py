"""Graph topology + routing.

Phase 3: triage → route_after_triage → {analysis | END}; analysis → action → END.
Phase 3.5 inserts action → eval → END.
"""

from langgraph.graph import START, END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import MemorySaver

from backend.config import settings
from backend.agents.state import MeridianState
from backend.agents.triage import triage_node
from backend.agents.analysis import analysis_node
from backend.agents.action import action_node

_graph: CompiledStateGraph | None = None


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
    graph.add_edge(START, "triage")
    graph.add_conditional_edges(
        "triage", route_after_triage, {"analysis": "analysis", END: END}
    )
    graph.add_edge("analysis", "action")
    graph.add_edge("action", END)
    return graph.compile(checkpointer=MemorySaver())


def get_graph() -> CompiledStateGraph:
    """Return the process-wide compiled graph, building it once on first use."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
