"""The graph must compile without raising."""

from langgraph.graph.state import CompiledStateGraph

from backend.agents.graph import build_graph


def test_build_graph_compiles() -> None:
    graph = build_graph()
    assert isinstance(graph, CompiledStateGraph)
