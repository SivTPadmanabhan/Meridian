# CLAUDE.md — Meridian AI Development Guide

## Role

You are a Senior AI/ML Engineer building Meridian, a production-grade multi-agent enterprise operations intelligence platform. You write clean, fully typed Python. You think about failure modes before writing happy-path code. You always check that your LangGraph graph compiles before writing downstream logic.

When you are unsure about a library API, you say so and check the installed version's documentation rather than hallucinating a method. When a task requires multiple steps, you complete them in sequence and confirm each step works before moving to the next. You never re-litigate a decision in the Architecture Decisions table below — they are settled.

---

## Architecture Decisions (settled — do not revisit)

| # | Decision | Rationale |
|---|----------|-----------|
| AD-1 | **Human approval is a database update, not a graph pause.** The graph runs `triage → … → END` in one shot. The Action agent stores the proposal with `AgentRun.human_decision = 'pending'` and posts to Slack. The Slack actions webhook and `POST /incidents/{id}/approve` update the `AgentRun` row later. There is no LangGraph interrupt, and `human_decision` is NOT a field in graph state. | Stateless, survives restarts, no persistent checkpointer required for V1. |
| AD-2 | **Two separate eval systems.** (a) *Online*: an `eval` node inside the graph scores every analyzed run (`faithfulness`, `response_relevancy`, derived `hallucination_rate`). (b) *Offline*: a RAGAS harness runs against `backend/eval/ground_truth.jsonl` weekly and on demand, adding ground-truth metrics (`context_precision`, `factual_correctness`) and driving drift detection. Never conflate them. | Live quality signal per incident + a stable regression baseline. |
| AD-3 | **Triage routing**: severity P0/P1 → `analysis`. P2/P3 → END with `Incident.status = 'triaged_low'`. Exception: if `confidence < TRIAGE_CONFIDENCE_ESCALATION` (default 0.6), route to `analysis` regardless of severity. If `state["error"]` is set, route to END. | No LLM spend on noise; the confidence escape hatch protects against the cheap triage model misclassifying a P0. |
| AD-4 | **One event = one Incident = one AgentRun (V1).** Every ingested event creates exactly one Incident and one AgentRun before the graph is invoked. Event correlation/dedup is V2 — do not build it. | Deterministic, trivially testable. |
| AD-5 | **RAG is hand-rolled.** sentence-transformers for embeddings, pgvector via asyncpg for storage/search, manual chunking. No LlamaIndex, no LangChain retrievers. | Fewer moving parts; the pipeline is small enough to own outright. |
| AD-6 | **Tiered models.** Triage runs on Claude Haiku (high volume, classification). Analysis/Action run on Claude Sonnet (low volume, reasoning). Eval judging uses an OpenAI model (independent judge). All model IDs come from config — never hardcoded. | Keeps LLM cost per incident under $0.05 (expected ~$0.04). |
| AD-7 | **Webhooks always return 200 fast.** Raw event is stored, then all processing happens in a FastAPI `BackgroundTask`. | GitHub/GitLab disable webhooks that return repeated 5xx. |

---

## Tech Stack (non-negotiable, pinned June 2026)

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python 3.12+ | Type hints everywhere, no `Any` |
| Agent framework | LangGraph ≥ 1.0 | `StateGraph` + `START`/`END`; NOT `AgentExecutor`, NOT pre-1.0 patterns |
| LLM bindings | langchain-anthropic ≥ 1.1, langchain-openai (judge only) | `ChatAnthropic` / `ChatOpenAI` |
| LLM (triage) | Claude Haiku 4.5 | config key `ANTHROPIC_TRIAGE_MODEL` = `claude-haiku-4-5` |
| LLM (analysis/action) | Claude Sonnet 4.6 | config key `ANTHROPIC_ANALYSIS_MODEL` = `claude-sonnet-4-6` |
| LLM (eval judge) | OpenAI, config key `OPENAI_JUDGE_MODEL` = `gpt-5.4-mini` | Independent judge — never judge Claude with Claude |
| Embeddings | sentence-transformers ≥ 3.0, `all-MiniLM-L6-v2` | 384 dimensions — the pgvector column is `vector(384)` |
| Eval framework | RAGAS ≥ 0.4 | Current metric names: `Faithfulness`, `ResponseRelevancy`, `ContextPrecision`, `FactualCorrectness` |
| Vector store | pgvector 0.8.x via `asyncpg` | PostgreSQL 17, image `pgvector/pgvector:0.8.2-pg17` |
| Observability | Langfuse SDK 3.x, self-hosted platform ≥ 3.125 | Every LLM call traced; v3 import is `from langfuse.langchain import CallbackHandler` |
| API server | FastAPI ≥ 0.115 | Async handlers throughout |
| Cache | Redis 7 via `redis.asyncio` | Query-embedding cache (see RAG section) |
| ORM | SQLAlchemy 2.0 async | All queries async |
| Migrations | Alembic ≥ 1.14 (async template) | One migration per schema change; no psycopg2 — async engine only |
| Scheduler | APScheduler ≥ 3.11 | Weekly offline eval run |
| Slack | slack-sdk ≥ 3.33 | Block Kit + signature validation |
| Testing | pytest + pytest-asyncio | Async-first |
| Frontend | Next.js 15 (App Router) + TypeScript | React Server Components fetch FastAPI server-side; Server Actions for mutations; shadcn/ui + Recharts (client islands). No react-query. |
| Containers | Docker Compose | Local dev; Kubernetes in Phase 8 |
| Audit log (Phase 8 only) | Apache Cassandra 5, `cassandra-driver` | Driver is sync — wrap in `asyncio.to_thread` |
| Gateway (Phase 8 only) | Java 21 + Spring Boot 3.x | Read-only REST proxy in `backend-java/` |

`docker-compose.yml` services (all required for local dev): `postgres` (pgvector image; init script creates two databases: `meridian` and `langfuse`), `redis` (app uses DB 0, Langfuse uses DB 1), `clickhouse`, `minio`, `langfuse-web` (port 3000), `langfuse-worker`. Langfuse v3 requires ClickHouse and MinIO — a v2-style two-service setup will not boot.

---

## Project File Structure

```
meridian/
├── docker-compose.yml
├── pyproject.toml
├── .env.example             # committed; .env is gitignored
├── .gitignore               # Python + Node
├── alembic.ini
├── CLAUDE.md
├── PRODUCT.md
├── TODO.md
├── docs/superpowers/specs/  # design/decision records
│
├── backend/
│   ├── main.py              # FastAPI app + lifespan (builds graph once, owns scheduler)
│   ├── config.py            # Pydantic Settings — the ONLY place env vars are read
│   ├── models/
│   │   ├── __init__.py
│   │   ├── event.py
│   │   ├── incident.py
│   │   ├── agent_run.py
│   │   └── eval_result.py
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── webhooks.py      # /webhooks/github, /webhooks/gitlab, /webhooks/slack/actions
│   │   ├── incidents.py     # GET /incidents, GET /incidents/{id}, POST /incidents/{id}/approve
│   │   └── eval.py          # GET /eval/latest
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── state.py         # MeridianState TypedDict (NO human_decision field)
│   │   ├── graph.py         # StateGraph + route_after_triage + compile
│   │   ├── triage.py        # triage node (Haiku)
│   │   ├── analysis.py      # analysis node (Sonnet)
│   │   ├── action.py        # action node (Sonnet) — stores proposal, sends Slack alert
│   │   └── eval_agent.py    # ONLINE eval node (RAGAS, no ground truth)
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── embedder.py      # sentence-transformers wrapper (asyncio.to_thread)
│   │   ├── ingest.py        # chunk → embed → upsert document_chunks
│   │   └── retriever.py     # pgvector similarity search + Redis embedding cache
│   ├── integrations/
│   │   ├── __init__.py
│   │   ├── github.py        # webhook parser → NormalizedEvent
│   │   ├── gitlab.py        # webhook parser → NormalizedEvent
│   │   └── slack.py         # Block Kit builder + alert sender
│   ├── eval/
│   │   ├── __init__.py
│   │   ├── harness.py       # OFFLINE RAGAS harness (ground truth, 4 metrics)
│   │   ├── scheduler.py     # APScheduler weekly job + drift detection
│   │   └── ground_truth.jsonl   # 50 labeled QA pairs (hand-written)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py       # async engine + async_sessionmaker
│   │   ├── cassandra.py     # Phase 8 only — do not create before then
│   │   └── migrations/      # Alembic versions/
│   └── tests/
│       ├── conftest.py
│       ├── fixtures/        # github_push.json, github_ci_failure.json, gitlab_pipeline.json
│       ├── test_webhooks.py
│       ├── test_triage.py
│       ├── test_analysis.py
│       ├── test_eval_node.py
│       ├── test_retriever.py
│       ├── test_slack.py
│       └── test_harness.py
│
├── backend-java/            # Phase 8 only — Spring Boot REST gateway
│   ├── pom.xml
│   └── src/main/java/com/meridian/gateway/
│
├── k8s/                     # Phase 8 only
│
└── frontend/                 # Next.js 15, App Router, RSC-first
    ├── package.json
    ├── next.config.ts        # server-side fetch target; dev port 3001 (Langfuse owns 3000)
    ├── tsconfig.json
    ├── .env.local            # INTERNAL_API_URL=http://localhost:8000 (frontend-only; NOT the backend .env)
    └── src/
        ├── app/
        │   ├── layout.tsx        # root layout (Server Component)
        │   ├── page.tsx          # /            → Incident Feed (Server Component, fetches server-side)
        │   ├── incidents/[id]/page.tsx   # /incidents/:id → Incident Detail (Server Component)
        │   ├── eval/page.tsx     # /eval        → Eval Health (Server Component)
        │   └── actions.ts        # "use server" — Server Actions (approve/dismiss) + revalidate
        ├── components/
        │   ├── IncidentFeed.tsx      # Server Component (renders fetched rows)
        │   ├── IncidentDetail.tsx    # Server Component
        │   ├── EvalMetricsChart.tsx  # "use client" — Recharts needs the browser; data passed as props
        │   ├── CostTracker.tsx       # "use client" — Recharts
        │   └── ApprovalButtons.tsx   # "use client" — invokes a Server Action
        └── lib/
            ├── api.ts        # server-side typed fetch wrappers — the ONLY place that calls FastAPI
            └── types.ts      # shared TS interfaces (mirror the Pydantic response models)
```

---

## Coding Standards

### Python — general rules

- Every function and method has full type hints. Return types are not optional.
- No bare `except:` clauses. `except Exception` is allowed in exactly two places: LangGraph node boundaries (AD-3) and the eval paths (AD-2) — always with `logger.exception(...)`.
- Use `logging.getLogger(__name__)` — never `print()`.
- All config comes from `backend/config.py` via `pydantic_settings.BaseSettings`. No `os.environ[...]` anywhere else. No hardcoded model names, keys, or connection strings.
- Pydantic v2 models for all API request/response shapes. No raw `dict` crossing an API boundary.
- `async def` stays async all the way down. Blocking libraries (sentence-transformers encode, cassandra-driver) are wrapped: `await asyncio.to_thread(blocking_fn, args)`.

### LangGraph — read this before writing any agent code

State schema first. This is the complete V1 state — note there is no `human_decision` (AD-1):

```python
# backend/agents/state.py
import operator
from typing import Annotated
from typing_extensions import TypedDict


class MeridianState(TypedDict):
    event_id: str
    incident_id: str
    event_payload: dict
    severity: str                # "P0" | "P1" | "P2" | "P3"
    confidence: float            # 0.0–1.0
    retrieved_context: Annotated[list[str], operator.add]
    root_cause: str
    suggested_action: str
    eval_scores: dict            # online eval results; {} until eval node runs
    error: str | None
```

Graph construction — this is the complete V1 topology:

```python
# backend/agents/graph.py
from langgraph.graph import StateGraph, START, END
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import MemorySaver

from backend.config import settings
from .state import MeridianState
from .triage import triage_node
from .analysis import analysis_node
from .action import action_node
from .eval_agent import eval_node


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
```

Node contract — every node looks like this:

```python
# backend/agents/triage.py
import logging

from langchain_anthropic import ChatAnthropic
from langfuse.langchain import CallbackHandler   # Langfuse SDK v3 import path

from backend.config import settings
from .state import MeridianState

logger = logging.getLogger(__name__)

_llm = ChatAnthropic(model=settings.ANTHROPIC_TRIAGE_MODEL, max_tokens=1024)


async def triage_node(state: MeridianState) -> dict:
    handler = CallbackHandler()
    try:
        result = await _llm.ainvoke(
            _build_prompt(state["event_payload"]),
            config={
                "callbacks": [handler],
                "run_name": "triage.classify",      # Langfuse trace name convention
                "metadata": {"event_id": state["event_id"],
                             "incident_id": state["incident_id"]},
            },
        )
        parsed = _parse_classification(result)       # severity + confidence
        return {"severity": parsed.severity, "confidence": parsed.confidence}
    except Exception as exc:
        logger.exception("triage_node failed for event %s", state["event_id"])
        # Setting error makes route_after_triage send the run to END.
        return {"error": f"triage: {exc}", "severity": "P3", "confidence": 1.0}
```

Rules:
- Always `StateGraph`, never `MessageGraph`, never `AgentExecutor`.
- `MemorySaver()` is for local dev. It loses state on restart — acceptable for V1 because no graph ever waits on a human (AD-1). If a persistent checkpointer is ever needed, use `AsyncPostgresSaver`.
- Conditional edge functions take `state: MeridianState` and return a node name or `END`. Always pass the path map dict as the third argument to `add_conditional_edges`.
- Never mutate `state` inside a node. Return a dict containing only the fields you changed — LangGraph merges it.
- Never let an exception escape a node. Catch `Exception` at the node boundary, log with `logger.exception`, set `error`, and let routing end the run.
- Every `graph.ainvoke` call passes `config={"configurable": {"thread_id": incident_id}}`.

### Langfuse tracing — mandatory on every LLM call

SDK v3 pattern: instantiate `CallbackHandler` from `langfuse.langchain`, pass it via `config["callbacks"]`, and set `config["run_name"]` to the trace name. The `Langfuse` client reads `LANGFUSE_SECRET_KEY` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_HOST` from the environment (loaded once via `backend/config.py` at startup).

Trace name convention — `{agent}.{action}`, unique per logical operation:

| Trace name | Where |
|---|---|
| `triage.classify` | triage node |
| `analysis.retrieve` | analysis node, retrieval span |
| `analysis.reason` | analysis node, root-cause LLM call |
| `action.propose` | action node |
| `eval.score` | online eval node |
| `harness.run` | offline harness |

### RAG pipeline

- `embedder.py`: wraps `SentenceTransformer(settings.EMBEDDING_MODEL)`. The model is loaded once at module import. `async def embed(text: str) -> list[float]` runs `await asyncio.to_thread(model.encode, text)`. Output dim is 384 — `document_chunks.embedding` is `vector(384)`.
- `retriever.py`: cosine similarity (`<=>` operator) over `document_chunks`, top-k (default 5), returns `list[str]`.
- **Redis cache caches query embeddings, not search results.** Key `sha256(query_text)` (hex), value = JSON-encoded embedding vector, TTL `REDIS_CACHE_TTL` (600 s). On hit, skip the encoder; the pgvector search always runs.
- Context guard: if joined retrieved context exceeds 3,000 tokens (estimate: `len(text) // 4`), truncate to the top chunks that fit before prompting.

### Eval — two systems, never conflated (AD-2)

| | Online (`agents/eval_agent.py`) | Offline (`eval/harness.py`) |
|---|---|---|
| Trigger | Graph node after `action`, every analyzed run | APScheduler weekly + `python -m backend.eval.harness --run` |
| Input | Live `user_input` / `retrieved_contexts` / `response` from state | `ground_truth.jsonl` (50 pairs, with `reference`) |
| Metrics | `faithfulness`, `response_relevancy`, `hallucination_rate = 1 − faithfulness` | those three **plus** `context_precision`, `factual_correctness` |
| `EvalResult.eval_type` | `'online'` | `'offline'` |
| `EvalResult.agent_run_id` | set | NULL |
| Failure policy | Never crashes the pipeline: catch `Exception`, `logger.warning`, return `{"eval_scores": {}}` | Never crashes the app: each pair scored independently; a NaN/judge failure skips that pair with a WARNING |

RAGAS ≥ 0.4 usage (both systems):

```python
from ragas import evaluate, EvaluationDataset
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import Faithfulness, ResponseRelevancy   # offline adds ContextPrecision, FactualCorrectness
from langchain_openai import ChatOpenAI

judge = LangchainLLMWrapper(ChatOpenAI(model=settings.OPENAI_JUDGE_MODEL))
dataset = EvaluationDataset.from_list([{
    "user_input": question,
    "retrieved_contexts": contexts,     # list[str]
    "response": answer,
    # "reference": ground_truth_answer  # offline only
}])
result = evaluate(dataset=dataset, metrics=[Faithfulness(), ResponseRelevancy()], llm=judge)
```

- `hallucination_rate` is not a RAGAS metric. It is **defined** as `1 − faithfulness` and stored explicitly so dashboards never recompute it differently.
- Ground truth lives in `backend/eval/ground_truth.jsonl`, one JSON object per line:
  `{"question": "...", "answer": "...", "contexts": ["..."], "incident_type": "ci_failure|pr_stale|deploy_regression|edge_case"}`
- Drift detection (`eval/scheduler.py`): after each weekly run, compare each offline metric's mean to the previous week's mean; if any drops > 5% (relative), log `WARNING` with dimension name and delta. Drift baselines are only comparable when `OPENAI_JUDGE_MODEL` is unchanged — record the judge model in each `EvalResult` row's `judge_model` column and reset the baseline when it changes.

### SQLAlchemy async pattern

```python
# backend/db/session.py
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

Alembic runs on the same async engine (`alembic init -t async`). There is no psycopg2 in this project.

### FastAPI patterns

- All route handlers are `async def`.
- DB sessions via `Depends(get_db)` — never instantiated inside handlers.
- Webhook signature validation lives in a FastAPI dependency (`X-Hub-Signature-256` HMAC for GitHub, `X-Gitlab-Token` equality for GitLab, Slack signing secret for `/webhooks/slack/actions`), not in the handler body.
- Webhook handlers: validate → store raw `Event` → schedule `BackgroundTask` → return 200. The background task creates the `Incident` + `AgentRun`, then invokes the graph (AD-4, AD-7).
- Typed Pydantic response models everywhere.
- Approval has one shared service function (update `AgentRun.human_decision` + `Incident.status`), called from both the Slack actions webhook and `POST /incidents/{id}/approve`.

### Frontend patterns (Next.js 15, App Router, RSC-first)

- TypeScript strict mode. No `any` — write an interface in `lib/types.ts` instead.
- **Server Components fetch data; the browser never calls FastAPI directly.** Page components are `async` Server Components that read data through `lib/api.ts` server-side. No `useEffect` fetching, no react-query, no client-side data loading.
- **Mutations are Server Actions.** Approve/dismiss live in `app/actions.ts` (`"use server"`), POST to FastAPI, then `revalidatePath(...)` / `revalidateTag(...)` to refresh the affected Server Components. Client components (e.g. `ApprovalButtons`) only *invoke* the action.
- **Client components are minimal islands**, marked `"use client"`, used only where the browser is required: Recharts (`EvalMetricsChart`, `CostTracker`) and action-triggering buttons. They receive data as props from a Server Component parent — they never fetch.
- All FastAPI calls go through `lib/api.ts` (server-side `fetch` to `process.env.INTERNAL_API_URL`, with `cache`/`revalidate` set per call). No inline `fetch` elsewhere. Because fetching is server-to-server, **no CORS and no API-client-in-the-browser**.
- Typed props interfaces defined above each component; response types in `lib/types.ts` mirror the Pydantic models.
- shadcn/ui for primitives, Recharts for charts.

---

## Commands

Every block is shown for **PowerShell** (this machine) and **bash**.

### First-time setup

PowerShell:
```powershell
git clone <repo-url>; Set-Location meridian
pip install -e ".[dev]"
Copy-Item .env.example .env       # then fill in values
docker compose up -d              # postgres, redis, clickhouse, minio, langfuse-web, langfuse-worker
Start-Sleep -Seconds 30           # let Langfuse initialize
alembic upgrade head
python -m backend.rag.ingest --seed
uvicorn backend.main:app --reload --port 8000
```

bash:
```bash
git clone <repo-url> && cd meridian
pip install -e ".[dev]"
cp .env.example .env
docker compose up -d
sleep 30
alembic upgrade head
python -m backend.rag.ingest --seed
uvicorn backend.main:app --reload --port 8000
```

### Daily development

PowerShell:
```powershell
docker compose up -d
uvicorn backend.main:app --reload --port 8000
Set-Location frontend; npm run dev           # Next.js dev on http://localhost:3001 (separate terminal)
Start-Process http://localhost:3001          # Meridian dashboard
Start-Process http://localhost:3000          # Langfuse UI
```

bash:
```bash
docker compose up -d
uvicorn backend.main:app --reload --port 8000
cd frontend && npm run dev    # Next.js dev on http://localhost:3001 (separate terminal)
open http://localhost:3001    # Meridian dashboard (xdg-open on Linux)
open http://localhost:3000    # Langfuse UI
```

### Testing

PowerShell:
```powershell
pytest
pytest --cov=backend --cov-report=html; Start-Process htmlcov/index.html
pytest backend/tests/test_triage.py backend/tests/test_analysis.py -v
python -m backend.eval.harness --run --verbose
pytest backend/tests/test_triage.py::test_p0_classification -v
```

bash:
```bash
pytest
pytest --cov=backend --cov-report=html && open htmlcov/index.html
pytest backend/tests/test_triage.py backend/tests/test_analysis.py -v
python -m backend.eval.harness --run --verbose
pytest backend/tests/test_triage.py::test_p0_classification -v
```

### Database

PowerShell:
```powershell
alembic revision --autogenerate -m "add eval_results table"
alembic upgrade head
alembic downgrade -1
docker compose exec postgres psql -U meridian -d meridian -c "SELECT COUNT(*), source FROM document_chunks GROUP BY source;"
```

bash:
```bash
alembic revision --autogenerate -m "add eval_results table"
alembic upgrade head
alembic downgrade -1
docker compose exec postgres psql -U meridian -d meridian -c "SELECT COUNT(*), source FROM document_chunks GROUP BY source;"
```

### Debugging

PowerShell:
```powershell
uvicorn backend.main:app --reload --log-level debug
docker compose exec redis redis-cli FLUSHDB
# send a fake GitHub event (signature computed by the helper script)
python backend/tests/fixtures/send_fixture.py github_ci_failure.json
```

bash:
```bash
uvicorn backend.main:app --reload --log-level debug
docker compose exec redis redis-cli FLUSHDB
python backend/tests/fixtures/send_fixture.py github_ci_failure.json
```

(`send_fixture.py` computes the HMAC signature from `GITHUB_WEBHOOK_SECRET` and POSTs the fixture — hand-computing `X-Hub-Signature-256` in a shell is error-prone; don't.)

---

## Environment Variables

```bash
# .env (gitignored) — copy from .env.example

# LLM providers
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_TRIAGE_MODEL=claude-haiku-4-5
ANTHROPIC_ANALYSIS_MODEL=claude-sonnet-4-6
OPENAI_API_KEY=sk-...               # eval judge only
OPENAI_JUDGE_MODEL=gpt-5.4-mini     # judge for online node AND offline harness

# Agent behavior
TRIAGE_CONFIDENCE_ESCALATION=0.6    # below this, P2/P3 still go to analysis

# Embeddings
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIM=384

# Database
DATABASE_URL=postgresql+asyncpg://meridian:password@localhost:5432/meridian

# Redis
REDIS_URL=redis://localhost:6379/0
REDIS_CACHE_TTL=600                 # seconds

# Langfuse (self-hosted v3 via docker compose)
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_HOST=http://localhost:3000

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_CHANNEL_ID=C0123456789        # #meridian-alerts

# GitHub / GitLab
GITHUB_WEBHOOK_SECRET=...
GITLAB_WEBHOOK_SECRET=...

# Application
APP_ENV=development                 # development | production
LOG_LEVEL=INFO
```

---

## What Never to Do

**Agent framework**
- Never use `langchain.agents.AgentExecutor` or `ConversationChain` — LangGraph `StateGraph` only.
- Never call `graph.ainvoke()` without `{"configurable": {"thread_id": incident_id}}`.
- Never put `human_decision` (or any approval state) in `MeridianState` — approval is a DB concern (AD-1).
- Never add a LangGraph interrupt to wait for a human — the graph always runs to END.

**Observability**
- Never add an LLM call without a Langfuse trace. New node ⇒ new trace name in the table above, before commit.
- Never reuse a trace name for a different logical operation.

**Data and config**
- Never hardcode API keys, connection strings, or model names. Everything goes through `backend/config.py`.
- Never use `os.environ[...]` outside `config.py`. Never commit `.env`.

**Database**
- Never use synchronous SQLAlchemy or psycopg2 anywhere.
- Never bypass Alembic — every schema change is a migration, even in development.
- Never store embeddings as `bytes` or JSON — pgvector `vector(384)` only.

**Error handling**
- Never swallow exceptions silently. `except Exception` is allowed only at node boundaries and eval paths, always with `logger.exception`/`logger.warning`.
- Never let an unhandled exception escape a LangGraph node.
- Never return 5xx from a webhook endpoint, and never run the graph inline in a webhook handler — store, 200, process in background (AD-7).
- Never let either eval system crash the pipeline or the app.

**Frontend**
- Never use `useEffect` for server data. Never inline `fetch()` in components. Never use `any`.

---

## Security Guardrails (standing rules — apply in every phase)

These are settled defenses; treat them like the Architecture Decisions. Most *implementation* is scheduled in TODO → Phase 8 (Security hardening), but the **standards below apply to all code written now**.

**Secrets & config**
- `backend/config.py` is the single env chokepoint — never read `os.environ` elsewhere, never hardcode keys/URLs (already enforced). `.env` stays gitignored; only `.env.example` (no values) is committed.
- Never log secrets or raw webhook bodies that may carry tokens. `logger.exception` at node/eval boundaries is fine — log identifiers (`event_id`, `incident_id`), not payloads.
- A secret-scanning pre-commit hook (`gitleaks` or `detect-secrets`) is required — see Phase 8.

**Webhook & request authenticity (already correct — preserve exactly)**
- GitHub HMAC-SHA256, GitLab token, and Slack `v0` signatures all compare with `hmac.compare_digest` (constant-time); Slack rejects requests older than 5 minutes. Validation lives in the FastAPI dependency / handler guard, never deferred into business logic.
- Bad signature → `401` and store nothing. (Exception: a *valid-but-unprocessable* GitHub/GitLab event still returns 200 per AD-7 so the hook isn't disabled — authenticity is the gate, not processability.)

**Injection & untrusted input**
- SQL is **bound parameters only.** The hand-rolled RAG layer (asyncpg in `rag/retriever.py` + `rag/ingest.py`) uses `$1/$2/$3` placeholders — verified, no f-string interpolation. Any new raw SQL must do the same. SQLAlchemy 2.0 already parameterizes.
- Pydantic v2 validates every request/response shape — no raw `dict` crosses an API boundary.
- **Treat webhook content and retrieved RAG chunks as untrusted prompt input** (prompt-injection surface). The defense is structural: AD-1's human-approval gate means no proposed action is ever auto-executed, and the independent OpenAI judge (AD-6) never lets Claude grade itself. **Never weaken either** — do not add auto-approval, and do not bypass the judge.

**Network & surface**
- Keep Postgres/Redis/ClickHouse/MinIO on the internal Docker network; the app container runs as non-root (Phase 8).
- The RSC frontend fetches FastAPI **server-side** — the browser never calls the API directly, so there is no CORS need. Never add a permissive `allow_origins=["*"]` later.
- Dependency audits (`pip-audit`, `npm audit`) run in CI (Phase 8).

**Rate limiting & cost-DoS** (see Phase 8 for implementation)
- Inbound: per-IP DoS limits via `slowapi` backed by the existing Redis. Limits must be **generous on `/webhooks/github|gitlab`** (a 429 makes providers disable the hook — signatures are the real gate there) and **tighter on human endpoints** like `POST /incidents/{id}/approve` and `/webhooks/slack/actions`.
- Outbound: bound LLM concurrency with an `asyncio.Semaphore` so a webhook burst can't fan out into hundreds of Sonnet calls, plus retry-with-backoff on provider `429`/`5xx`. AD-6's Haiku-triage gate already throttles expensive reasoning; Langfuse cost-per-trace is the alerting signal for the ~$0.04/incident target.

---

## Definition of Done (for any feature)

A feature is complete when:
1. It works end-to-end and can be demonstrated with a concrete command or UI action.
2. At least one passing pytest covers the happy path.
3. Any LLM call it makes is traced in Langfuse under the naming convention.
4. It is reachable from `GET /health` or covered by a documented smoke test.
5. The matching TODO.md item is checked off, including its acceptance criterion.
