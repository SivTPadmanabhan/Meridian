# PRODUCT.md — Meridian

## Vision

Meridian turns fragmented engineering signals into actionable, prioritized intelligence — surfaced in Slack and a Next.js dashboard before an engineer needs to look. It is not a chatbot. It is a self-evaluating multi-agent system that watches your ops stack, triages noise from signal, performs root cause analysis with RAG context, and proposes concrete actions with a human approval gate before anything consequential happens.

**V1 scope is deliberately narrow:** GitHub and GitLab CI as the only two data sources. One vertical (DevOps), done right, with a working eval harness. Everything else is V2.

---

## The Problem

Engineering teams are drowning in signal noise. CI failures arrive in GitLab. PR reviews stall in GitHub. Incident threads scatter across Slack. By the time a human has triaged all three, correlated them, and looked up the relevant runbook, 40 minutes have passed and a customer has already noticed. The root cause was often obvious — if you knew where to look.

The problem is not a shortage of monitoring tools. It is a shortage of intelligence that spans all of them simultaneously.

---

## Target User (V1)

**Alex — DevOps Lead at a 50-person startup**

- Manages a 3-person infra team and is on-call every other week
- Has 5+ monitoring tools open in browser tabs at all times
- Gets woken up at 2am for alerts that have obvious root causes if you check the last three deploys
- Cares deeply about mean time to acknowledge (MTTA) and has tried every alert aggregator without success
- Would immediately trust a system that shows its reasoning before asking for an approval click

**Alex's win state:** Meridian catches a CI failure at 2:04am, finds the matching past incident from six weeks ago, identifies the offending commit, drafts the runbook link, and posts a structured Slack message — with an Approve button — before Alex's phone rings. Alex clicks Approve from Slack and goes back to sleep.

---

## User Stories

### Must ship (V1 core pipeline)

**US-001** — As a DevOps lead, I want Meridian to automatically ingest GitHub push events, PR events, and CI/CD status events so I don't need to manually monitor multiple dashboards.

**US-002** — As a DevOps lead, I want Meridian to classify each incoming event by severity (P0–P3) with a confidence score so I can trust the triage output without reviewing every event personally.

**US-003** — As a DevOps lead, I want Meridian to retrieve contextually similar past incidents from the knowledge base before reasoning, so the Analysis agent has relevant historical context instead of hallucinating.

**US-004** — As a DevOps lead, I want Meridian to post a structured Slack alert containing severity, root cause summary, affected services, a suggested runbook link, and a one-click Approve/Dismiss button so I can act immediately from Slack without opening another tool.

**US-005** — As an AI engineer, I want all LLM calls to be traced in Langfuse with latency, cost, and quality scores so I can monitor system health and audit any individual decision.

### Must ship (V1 eval)

**US-006** — As an AI engineer, I want every analyzed incident scored **online** by an eval node (faithfulness, response relevancy, derived hallucination rate) so each live run carries its own quality signal.

**US-007** — As an AI engineer, I want an **offline** RAGAS harness that scores 50 hand-written ground-truth pairs (adding context precision and factual correctness) weekly and on demand, with a drift alert when any dimension drops more than 5% week-over-week, so regressions are caught against a stable baseline without manual monitoring.

### V1.5 (dashboard — build after the pipeline works)

**US-008** — As a DevOps lead, I want a Next.js dashboard showing active incidents, agent confidence scores over time, and LLM cost per query so I have a single pane of glass view.

**US-009** — As a DevOps lead, I want to see the full agent trace for any past incident — input event, retrieved context, LLM reasoning, action taken, eval scores — so I can audit what the system decided and why.

**US-010** — As a DevOps lead, I want an approval queue in the dashboard showing all pending action proposals so I can batch-approve from a web UI as an alternative to Slack.

### V2 (do not build until V1 is shipped and evaluated)

**US-011** — Slack message threads as a RAG knowledge source.

**US-012** — Salesforce pipeline events ingested alongside engineering signals (RevOps vertical).

**US-013** — Automatic post-mortem generation from an incident's full agent trace.

**US-014** — Event correlation: multiple related events grouped into one incident (V1 is strictly 1 event = 1 incident).

---

## Design Layout

### System Architecture

```
┌─ Ingest Layer ───────────────────────────────────────────────┐
│  POST /webhooks/github   — HMAC-validated, ALWAYS returns 200 │
│  POST /webhooks/gitlab   — token-validated, ALWAYS returns 200│
│  Store raw Event → BackgroundTask (processing never blocks    │
│  the webhook response)                                        │
└──────────────────────────────────────────────────────────────┘
                            ↓
┌─ Storage Layer ──────────────────────────────────────────────┐
│  PostgreSQL 17 + pgvector — events, incidents, agent_runs,    │
│                             eval_results, document_chunks     │
│  Redis                    — query-embedding cache             │
└──────────────────────────────────────────────────────────────┘
                            ↓  (1 event → 1 Incident → 1 AgentRun)
┌─ Agent Layer (one LangGraph StateGraph run, no pauses) ──────┐
│  Triage (Haiku)     — severity P0–P3 + confidence             │
│     ├─ P2/P3 confident → END (incident status: triaged_low)   │
│     └─ P0/P1 or low confidence ↓                              │
│  Analysis (Sonnet)  — RAG retrieval + root cause              │
│  Action (Sonnet)    — proposal stored (human_decision=pending)│
│                       + Slack alert posted                    │
│  Eval (online)      — RAGAS scores for THIS run → END         │
└──────────────────────────────────────────────────────────────┘
                            ↓                       ↑ weekly
┌─ Approval (asynchronous, outside the graph) ─────┐ ┌─ Offline ─┐
│  Slack button → /webhooks/slack/actions          │ │ RAGAS     │
│  Dashboard    → POST /incidents/{id}/approve     │ │ harness + │
│  Both update AgentRun.human_decision + Incident  │ │ drift     │
└───────────────────────────────────────────────────┘ └───────────┘
                            ↓
┌─ Output Layer ───────────────────────────────────────────────┐
│  Slack Bot         — Block Kit alerts + approval buttons      │
│  Next.js Dashboard — RSC + Recharts + shadcn/ui               │
│  Langfuse (v3)     — traces, latency, cost per call           │
└──────────────────────────────────────────────────────────────┘
```

### API Surface (FastAPI)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/webhooks/github` | Receive GitHub events (push, PR, check_run). Always 200; processing is async |
| `POST` | `/webhooks/gitlab` | Receive GitLab pipeline/job events. Always 200; processing is async |
| `POST` | `/webhooks/slack/actions` | Slack button interactions (approve/dismiss) |
| `GET`  | `/incidents` | Recent incidents (joined to latest AgentRun): status, severity, confidence |
| `GET`  | `/incidents/{id}` | Full trace for one incident including all agent steps and eval scores |
| `POST` | `/incidents/{id}/approve` | Approve or dismiss a proposal (body: `{"decision": "approved"\|"dismissed"}`). Shared handler with the Slack path |
| `GET`  | `/eval/latest` | Last 30 days of eval scores, aggregated per day and per `eval_type` |
| `GET`  | `/health` | Liveness — checks DB, Redis, and Langfuse connectivity |

### Data Models

**Event** — a single raw ingested signal
```
id, source (github|gitlab), event_type, payload (jsonb),
raw_body, received_at, processed_at
```

**Incident** — V1 rule: exactly one per event (correlation is V2 / US-014)
```
id, event_id (FK, unique), severity (P0-P3),
status (open | triaged_low | approved | dismissed | resolved),
title, created_at, resolved_at
```

**AgentRun** — one full LangGraph pipeline execution (V1: exactly one per incident)
```
id, incident_id (FK), langfuse_trace_id, triage_output (jsonb),
analysis_output (jsonb), action_proposed (jsonb),
human_decision (approved | dismissed | pending | null),   ← null for triaged_low runs
completed_at
```

**EvalResult** — one row per scored run (online) or per scored ground-truth pair (offline)
```
id, eval_type ('online' | 'offline'),
agent_run_id (FK, NULL for offline rows),
faithfulness (float), response_relevancy (float),
hallucination_rate (float, stored as 1 − faithfulness),
context_precision (float, NULL for online rows),
factual_correctness (float, NULL for online rows),
judge_model (text), scored_at
```

### Slack Message Structure

Each Slack alert contains:
1. **Header block** — severity badge (P0 = red, P1 = orange, P2 = yellow, P3 = gray) + incident title
2. **Context block** — source repo/pipeline, timestamp, triage confidence
3. **Section block** — root cause summary (2–3 sentences, LLM-generated)
4. **Section block** — retrieved context sources (top-2 similar past incidents, linked)
5. **Section block** — suggested action (one concrete remediation step)
6. **Actions block** — green "Approve action" button + gray "Dismiss" button

### Next.js Dashboard Pages

- `/` — **Incident Feed**: paginated list, severity badges, time since first event, status chip
- `/incidents/:id` — **Incident Detail**: full agent trace, retrieved context viewer, online eval scores, approval history
- `/eval` — **Eval Health**: line charts for offline metrics over 30 days with target reference lines, drift alerts, online-vs-offline comparison
- `/costs` — **LLM Costs**: cost-per-day bar chart, per-model breakdown (Haiku vs Sonnet vs judge), cost-per-incident trend

---

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| MTTA reduction | ≥ 40% vs. baseline | Compare timestamp: event ingested → human first action |
| P95 retrieval latency | < 2 seconds | Langfuse span timing on `analysis.retrieve` |
| Faithfulness (offline) | ≥ 0.85 | `eval_results` where `eval_type='offline'`, weekly |
| Hallucination rate (= 1 − faithfulness) | ≤ 0.10 | `eval_results`, triggers drift alert if exceeded |
| Human approval rate | ≥ 70% | `agent_runs.human_decision` over analyzed runs |
| LLM cost per incident | < $0.05 (expected ~$0.04 with Haiku triage + Sonnet analysis) | Langfuse cost per trace |
