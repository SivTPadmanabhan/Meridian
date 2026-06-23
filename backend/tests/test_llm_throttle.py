"""Phase 8 — outbound LLM concurrency throttle.

AC: firing 50 runs concurrently never exceeds LLM_MAX_CONCURRENCY in-flight graph
invocations (and therefore in-flight provider calls, since a run's LLM calls are
sequential). Verified hermetically via an in-flight counter — no real LLM needed.
"""

import asyncio

from backend.config import settings
from backend.agents import graph as graph_mod


async def test_semaphore_bounds_concurrent_graph_runs(monkeypatch):
    original = settings.LLM_MAX_CONCURRENCY
    settings.LLM_MAX_CONCURRENCY = 4
    graph_mod._semaphores.clear()  # force a fresh semaphore at the new limit
    try:
        tracker = {"running": 0, "peak": 0}

        class _FakeGraph:
            async def ainvoke(self, initial_state, config):
                tracker["running"] += 1
                tracker["peak"] = max(tracker["peak"], tracker["running"])
                await asyncio.sleep(0.02)
                tracker["running"] -= 1
                return {"ok": True}

        monkeypatch.setattr(graph_mod, "get_graph", lambda: _FakeGraph())

        await asyncio.gather(
            *(graph_mod.ainvoke_graph({}, str(i)) for i in range(50))
        )

        # Never exceeds the cap, and with 50 >> 4 it should saturate exactly to it.
        assert tracker["peak"] <= 4, tracker["peak"]
        assert tracker["peak"] == 4, tracker["peak"]
        assert tracker["running"] == 0
    finally:
        settings.LLM_MAX_CONCURRENCY = original
        graph_mod._semaphores.clear()
