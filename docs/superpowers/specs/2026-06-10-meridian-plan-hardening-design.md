# Meridian Plan Hardening — Decision Record

**Date:** 2026-06-10
**Scope:** Full rewrite of CLAUDE.md, PRODUCT.md, TODO.md so a less capable model can execute the plan without guessing. No code was written; the three rewritten documents are the operative spec. This file is the audit trail of what was wrong and what was decided.

---

## Contradictions found in the original documents

1. **Two eval systems conflated.** CLAUDE.md's graph wired `eval` as a pipeline node, while the RAGAS section and TODO Phase 4 described an offline ground-truth harness; TODO never added an eval node to the graph at all.
2. **Metric mismatch.** PRODUCT.md tracked `hallucination_rate` (not a RAGAS metric); TODO Phase 4 scored `answer_correctness`; the `EvalResult` model matched neither set.
3. **Approval flow half-specified twice.** `MeridianState` carried `human_decision`, implying a graph pause, while Phase 5 had the Slack webhook updating the database — two incompatible designs.
4. **LlamaIndex declared but never used.** The stack named LlamaIndex as the RAG layer; every implementation step built RAG by hand.
5. **`route_after_triage` referenced but undefined.** The fate of P2/P3 events was unspecified.
6. **Incident creation logic absent.** No TODO item ever created an `Incident` row; `GET /incidents` returned `AgentRun` rows.
7. **`POST /incidents/{id}/approve`** existed in the API table and the dashboard but not in the file structure or any TODO item.
8. **Stale 2024 pins** (claude-3-5-sonnet-20241022 — retired Oct 2025; GPT-4o; LangGraph 0.2; Langfuse v2 imports; LlamaIndex 0.10) in a project dated June 2026.
9. **Bash/macOS-only commands** on a Windows 11 dev machine.
10. **Type errors in canonical snippets** (e.g., `build_graph() -> StateGraph` instead of the compiled graph type) that a weaker model would copy verbatim.

---

## Decisions (all confirmed with the user, 2026-06-09/10)

| # | Decision |
|---|----------|
| 1 | Update the whole stack to current stable versions, verified by research, pinned in CLAUDE.md. |
| 2 | Approval = DB update. Graph runs to END; `human_decision` removed from graph state; Slack webhook and `POST /incidents/{id}/approve` share one service function. No LangGraph interrupt. |
| 3 | Both eval systems, explicitly separated: online eval node (per analyzed run, no ground truth) + offline harness (ground truth, weekly, drift detection). |
| 4 | Triage routing: P0/P1 → analysis; P2/P3 → END as `triaged_low`; confidence < 0.6 (configurable) escalates to analysis regardless of severity; `error` → END. |
| 5 | V1 incident rule: 1 event = 1 Incident = 1 AgentRun. Correlation deferred to V2 (new US-014). |
| 6 | LlamaIndex cut; hand-rolled RAG is canonical. psycopg2 dropped; Alembic on the async engine. |
| 7 | Metric split: online = faithfulness, response_relevancy, hallucination_rate (≝ 1 − faithfulness); offline adds context_precision, factual_correctness. `EvalResult` gains `eval_type`, nullable columns, `judge_model`. RAGAS ≥ 0.4 names used throughout. |
| 8 | Phase 8 keeps Cassandra audit log and the Java Spring Boot gateway — both expanded into fully specified sub-tasks (user chose to keep them; resume value). |
| 9 | All command blocks shown in PowerShell and bash. |
| 10 | Models: `claude-haiku-4-5` (triage), `claude-sonnet-4-6` (analysis/action), OpenAI `gpt-5.4-mini` (judge, configurable). Cost target stays < $0.05/incident (expected ~$0.04). Chosen over Opus-4.8 analysis (~$0.053/call), which alone would have broken the documented cost metric. |
| 11 | Full rewrite of all three documents (user chose over surgical/hybrid). |

## Version research (June 2026)

| Component | Pin | Load-bearing API facts |
|---|---|---|
| LangGraph | ≥ 1.0 (v1.0 GA Oct 2025) | `from langgraph.graph import StateGraph, START, END`; `compile()` → `CompiledStateGraph`; `MemorySaver` dev-only, `AsyncPostgresSaver` for prod |
| Langfuse | SDK 3.x; platform ≥ 3.125 | `from langfuse.langchain import CallbackHandler`; self-host compose needs postgres + clickhouse + redis + minio + web + worker |
| RAGAS | ≥ 0.4 (0.4.3 Jan 2026) | Metrics renamed: `ResponseRelevancy`, `FactualCorrectness`; `EvaluationDataset.from_list`; sample fields `user_input/retrieved_contexts/response/reference`; judge via `LangchainLLMWrapper` |
| langchain-anthropic | ≥ 1.1 | `ChatAnthropic(model=...)` |
| OpenAI judge | `gpt-5.4-mini` default | GPT-4o stale; current lineup GPT-5.4/5.5; judge model recorded per EvalResult row for drift-baseline validity |
| pgvector | `pgvector/pgvector:0.8.2-pg17` | `vector(384)` for all-MiniLM-L6-v2 |

## Target architecture (summary)

`triage(Haiku) → route_after_triage → {analysis(Sonnet) | END}`; `analysis → action(Sonnet) → eval(online RAGAS) → END`. Webhooks always 200; background task creates Event/Incident/AgentRun then `graph.ainvoke(thread_id=incident_id)`. Approval updates `AgentRun.human_decision` + `Incident.status` via one shared service from two entry points. Offline harness runs weekly via APScheduler with judge-model-aware drift detection. Full details: CLAUDE.md → Architecture Decisions.
