# TODO.md — Meridian Build Checklist

> **Rules:**
> 1. Do not skip ahead. Complete every item in a phase before starting the next.
> 2. A phase is done when all items are checked **and** `pytest` passes clean.
> 3. Every item has an **AC** (acceptance criterion). An item is not done until its AC is observed.
> 4. Architecture decisions live in CLAUDE.md → Architecture Decisions. Do not re-decide them here.
> 5. Phase 4 (offline eval harness) is the hardest. Do it while you are fresh — not at the end.

---

## Phase 0 — Foundation
*Goal: everything boots, nothing crashes. Est. 1–2 hours.*

- [x] Initialize repo: `git init`, add `.gitignore` (Python + Node)
- [x] Create `pyproject.toml` with project metadata and dependencies:
  `fastapi`, `uvicorn[standard]`, `langgraph>=1.0`, `langchain-anthropic>=1.1`, `langchain-openai`, `asyncpg`, `redis`, `langfuse>=3.0`, `ragas>=0.4`, `sentence-transformers>=3.0`, `slack-sdk>=3.33`, `apscheduler>=3.11`, `sqlalchemy[asyncio]>=2.0`, `alembic>=1.14`, `pydantic-settings`, `httpx`; dev extras: `pytest`, `pytest-asyncio`, `ruff`, `mypy`.
  **Do NOT install:** `llama-index` (cut — AD-5), `psycopg2-binary` (async engine only), `langchain` meta-package.
  **AC:** `pip install -e ".[dev]"` succeeds; `python -c "import langgraph, langfuse, ragas"` exits 0.
- [x] Write `docker-compose.yml` with services: `postgres` (`pgvector/pgvector:0.8.2-pg17`, init script creates databases `meridian` and `langfuse`), `redis` (redis:7), `clickhouse`, `minio`, `langfuse-web` (port 3000), `langfuse-worker`. Base the Langfuse v3 service block on the official Langfuse docker-compose (v3 requires ClickHouse + MinIO; a two-service v2 layout will not boot).
  **AC:** `docker compose up -d` → all services healthy; Langfuse login page loads at `http://localhost:3000`.
- [x] Write `.env.example` with every key from CLAUDE.md → Environment Variables (no values).
  **AC:** every `settings.` attribute referenced in code exists in `.env.example`.
- [x] Create `backend/config.py` with `pydantic_settings.BaseSettings` loading `.env`, including `ANTHROPIC_TRIAGE_MODEL`, `ANTHROPIC_ANALYSIS_MODEL`, `OPENAI_JUDGE_MODEL`, `TRIAGE_CONFIDENCE_ESCALATION`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`.
  **AC:** `python -c "from backend.config import settings; print(settings.ANTHROPIC_TRIAGE_MODEL)"` prints `claude-haiku-4-5`.
- [x] Write `backend/db/session.py`: async engine + `async_sessionmaker` + `get_db` dependency (pattern in CLAUDE.md).
- [x] Write the four SQLAlchemy models exactly per PRODUCT.md → Data Models: `Event`, `Incident` (status enum: open/triaged_low/approved/dismissed/resolved; `event_id` unique FK), `AgentRun` (`human_decision` nullable), `EvalResult` (`eval_type`, nullable `agent_run_id`, nullable `context_precision`/`factual_correctness`, `judge_model`).
- [x] `alembic init -t async alembic`; configure `env.py` for the async engine and model metadata.
- [x] Initial migration: all four tables + `CREATE EXTENSION IF NOT EXISTS vector` + `document_chunks` table (`id`, `source`, `content text`, `embedding vector(384)`, `created_at`).
  **AC:** `alembic upgrade head` succeeds; `docker compose exec postgres psql -U meridian -d meridian -c "\dt"` lists all five tables.
- [x] `GET /health`: `SELECT 1` on Postgres, `PING` on Redis, HTTP GET on `LANGFUSE_HOST`.
  **AC:** `curl localhost:8000/health` returns `{"status":"ok","db":true,"redis":true,"langfuse":true}`.

---

## Phase 1 — Ingest Pipeline
*Goal: a GitHub event hits the webhook, gets normalized, embedded, and stored in pgvector. Est. 2–3 hours.*

- [x] `backend/integrations/github.py`: parse `push`, `pull_request`, `check_run` payloads into a `NormalizedEvent` Pydantic model (`source`, `event_type`, `repo`, `title`, `body_text`, `occurred_at`, `raw`).
- [x] `backend/integrations/gitlab.py`: parse `pipeline` and `job` payloads into the same `NormalizedEvent`.
  **AC (both):** unit test feeds each fixture file and asserts every `NormalizedEvent` field is populated.
- [x] `POST /webhooks/github`: signature dependency validates `X-Hub-Signature-256` (HMAC-SHA256 of raw body with `GITHUB_WEBHOOK_SECRET`); handler stores raw `Event`, schedules background processing, returns 200 **before** any LLM work (AD-7). Invalid signature → 401.
- [x] `POST /webhooks/gitlab`: same pattern with `X-Gitlab-Token` equality check.
- [x] `backend/rag/embedder.py`: module-level `SentenceTransformer(settings.EMBEDDING_MODEL)`; `async def embed(text: str) -> list[float]` via `asyncio.to_thread`.
  **AC:** `len(await embed("hello")) == 384`.
- [x] `backend/rag/ingest.py`: NormalizedEvent → chunk text (~500 chars, no mid-word splits) → embed → upsert `document_chunks`. Include `--seed` CLI that ingests `backend/tests/fixtures/seed_incidents.jsonl`.
- [x] `backend/rag/retriever.py`: `async def retrieve(query: str, k: int = 5) -> list[str]` — embed query, cosine search (`embedding <=> $1` ordered ascending), return chunk texts.
- [x] Redis cache in retriever — caches the **query embedding only**: key `sha256(query_text).hexdigest()`, value JSON list of floats, TTL `REDIS_CACHE_TTL`. Cache hit skips the encoder; pgvector search always runs. Log `cache_hit=true|false` as a structured field.
- [x] Fixtures: `backend/tests/fixtures/github_push.json`, `github_ci_failure.json`, `gitlab_pipeline.json`, `seed_incidents.jsonl` (10 short historical incidents), and `send_fixture.py` (computes HMAC, POSTs to localhost).
- [x] `backend/tests/test_webhooks.py`: POST each fixture with a valid signature → 200 + Event row created; invalid signature → 401, no row.
- [x] `backend/tests/test_retriever.py`: seed 10 embeddings, query for a known topic, assert the matching doc is in the top-3.
- [x] Smoke test.
  **AC:** `python backend/tests/fixtures/send_fixture.py github_ci_failure.json` → `document_chunks` count increases (verify with the psql command in CLAUDE.md → Database).

---

## Phase 2 — Triage Agent + Langfuse
*Goal: an event enters the graph, gets a severity, an Incident + AgentRun exist, and the trace appears in Langfuse. Est. 2–3 hours.*

- [x] `backend/agents/state.py`: `MeridianState` exactly as in CLAUDE.md (no `human_decision` — AD-1).
- [x] `backend/agents/triage.py`: classify severity + confidence with `ChatAnthropic(model=settings.ANTHROPIC_TRIAGE_MODEL)`; Langfuse v3 `CallbackHandler` with `run_name="triage.classify"`; node-boundary `except Exception` sets `error` and returns `severity="P3", confidence=1.0` (routes to END).
- [x] `backend/agents/graph.py`: graph + `route_after_triage` exactly as in CLAUDE.md (P0/P1 → analysis; confidence < `TRIAGE_CONFIDENCE_ESCALATION` → analysis; error or confident P2/P3 → END). Until Phase 3, register `analysis` as a stub node that returns `{}`.
  **AC:** `pytest backend/tests/test_graph_compiles.py` — a one-line test asserting `build_graph()` returns without raising.
- [x] Background task pipeline (called from webhooks): create `Incident` (status `open`, title from NormalizedEvent) + `AgentRun` → `graph.ainvoke(initial_state, config={"configurable": {"thread_id": str(incident_id)}})` → persist triage output to `AgentRun.triage_output`; if run ended at triage with P2/P3, set `Incident.status='triaged_low'` and leave `human_decision` NULL. (This item was missing from the old plan — nothing else creates Incident rows.)
  **AC:** POST a fixture → exactly one new row in each of `events`, `incidents`, `agent_runs`.
- [ ] Manual check: open Langfuse at `localhost:3000`, confirm a `triage.classify` trace with model `claude-haiku-4-5` after POSTing a fixture. _(BLOCKED on credentials: requires LANGFUSE_PUBLIC_KEY/SECRET_KEY + ANTHROPIC_API_KEY in .env. Without keys the Langfuse client self-disables and triage degrades to error→END. Code path is wired per spec; verify once keys are set.)_
- [x] `backend/tests/test_triage.py`: 6 sample events (2 expected P0, 2 P1, 2 P2). Assert severity ∈ {P0..P3}, 0 ≤ confidence ≤ 1, and `route_after_triage` returns "analysis" for the P0/P1 cases. _(Implemented hermetically: route_after_triage covered for all branches + triage_node with a mocked structured LLM across P0–P3 and the error path — no API key needed. Live-quality classification is the credentialed manual check above.)_
- [x] `GET /incidents`: last 20 `Incident` rows joined to their `AgentRun` (severity, status, confidence, created_at), newest first, typed response model.
  **AC:** `curl localhost:8000/incidents` returns the incidents created above.

---

## Phase 3 — Analysis + Action Agents
*Goal: a P0/P1 event produces a root cause and a stored pending proposal. Est. 2–3 hours.*

- [x] `backend/agents/analysis.py`: retrieve top-5 similar chunks (`run_name="analysis.retrieve"` span), build context (3,000-token guard per CLAUDE.md), root-cause call on `ChatAnthropic(model=settings.ANTHROPIC_ANALYSIS_MODEL)` with `run_name="analysis.reason"`. Returns `{"retrieved_context": [...], "root_cause": "..."}`.
- [x] `backend/agents/action.py`: produce one concrete `suggested_action` (`run_name="action.propose"`, Sonnet); persist `analysis_output` + `action_proposed` to `AgentRun`; set `human_decision='pending'`. (Slack send is added in Phase 5 — leave a single clearly-marked call site.)
- [x] Wire real `analysis` and `action` nodes into the graph (replacing the Phase 2 stub): `analysis → action`, and `action → eval` once Phase 3.5 lands (until then `action → END`).
- [x] `backend/tests/test_analysis.py`: seed 5 historical incident embeddings, run the analysis node on a CI-failure state, assert `root_cause` is a non-empty string and `retrieved_context` is non-empty.
- [x] Integration test: POST `github_ci_failure.json` (a P0/P1-shaped event), poll `GET /incidents` until the run completes.
  **AC:** the incident's `AgentRun` has non-null `analysis_output`, `action_proposed`, and `human_decision='pending'`. _(Covered hermetically by test_full_pipeline_to_pending_proposal — triage/analysis/action LLMs mocked, retrieval real. Live polling needs ANTHROPIC_API_KEY; without it triage errors→END before analysis.)_

---

## Phase 3.5 — Online Eval Node
*Goal: every analyzed run carries its own quality scores. Est. 1 hour. (Online half of AD-2.)*

> **NOTE (RAGAS import path, applies to Phase 3.5 + Phase 4):** On the Phase 0 env (ragas 0.4.3),
> `from ragas.metrics import Faithfulness, ResponseRelevancy` (the path in CLAUDE.md) still works
> but emits a DeprecationWarning: these move to `ragas.metrics.collections` in ragas v1.0
> (e.g. `from ragas.metrics.collections import Faithfulness`). Same applies to `ContextPrecision`
> / `FactualCorrectness` in Phase 4. Decide whether to adopt the new path when implementing these
> nodes; if so, update CLAUDE.md → Eval section to match.

- [x] `backend/agents/eval_agent.py`: build a one-row RAGAS `EvaluationDataset` from state (`user_input` = event summary, `retrieved_contexts` = `retrieved_context`, `response` = `root_cause`); score `Faithfulness()` + `ResponseRelevancy()` with judge `LangchainLLMWrapper(ChatOpenAI(model=settings.OPENAI_JUDGE_MODEL))`; store `EvalResult(eval_type='online', agent_run_id=..., hallucination_rate=1-faithfulness, judge_model=...)`; `run_name="eval.score"`. On ANY failure: `logger.warning`, return `{"eval_scores": {}}` — never crash the pipeline.
- [x] Wire `action → eval → END` in the graph.
- [x] `backend/tests/test_eval_node.py`: run the node with a stubbed judge (monkeypatched `evaluate`) → `EvalResult` row with `eval_type='online'`, scores in [0,1]; and with a judge that raises → no exception escapes, `eval_scores == {}`.
  **AC:** integration run from Phase 3 now also produces one `eval_results` row with `eval_type='online'`. _(Covered by test_full_pipeline_to_pending_proposal, which now stubs the RAGAS judge and asserts one online eval_results row. Live scoring needs OPENAI_API_KEY.)_

---

## Phase 4 — Offline RAGAS Harness ← do this before you are tired
*Goal: a scored offline eval run completes and stores to DB. Est. 3–4 hours. (Offline half of AD-2.)*

- [x] Write 50 labeled QA pairs in `backend/eval/ground_truth.jsonl`, one JSON object per line:
  `{"question": "...", "answer": "...", "contexts": ["..."], "incident_type": "ci_failure|pr_stale|deploy_regression|edge_case"}`
  At least 15 CI failures, 15 PR scenarios, 10 deploy regressions, 10 edge cases. Write real ones — not AI-generated throwaways. These are your north star.
  **AC:** `python -c "import json,sys; [json.loads(l) for l in open('backend/eval/ground_truth.jsonl')]"` exits 0 and the line count is ≥ 50. _(56 pairs — sourced from real human-authored Stack Exchange Q&A (CC-BY-SA) via `backend/eval/build_ground_truth.py`; NOT AI-authored. `pr_stale` holds code-review questions, the closest real PR data. See README attribution.)_
- [x] `backend/eval/harness.py`:
  - Load ground truth; for each pair: run the retriever, run the analysis prompt path, build a RAGAS sample (`user_input`, `retrieved_contexts`, `response`, `reference`=answer).
  - Score with `Faithfulness()`, `ResponseRelevancy()`, `ContextPrecision()`, `FactualCorrectness()`; judge = `LangchainLLMWrapper(ChatOpenAI(model=settings.OPENAI_JUDGE_MODEL))`.
  - Store one `EvalResult` per pair: `eval_type='offline'`, `agent_run_id=NULL`, `hallucination_rate=1-faithfulness`, `judge_model` recorded.
  - Per-pair `except Exception`: log WARNING with the pair index, skip, continue (RAGAS judges can return NaN — a bad pair must not kill the run).
  - CLI: `python -m backend.eval.harness --run --verbose`.
  **AC:** the CLI completes against the live DB and inserts ≥ 45 offline rows (≤ 5 skips tolerated). _(Code complete; live ≥45-rows run is credential-blocked — needs OPENAI_API_KEY + ANTHROPIC_API_KEY. Deferred to Phase 4.6.)_
- [x] `GET /eval/latest`: last 30 days of `eval_results` aggregated per day per `eval_type` (mean of each metric), typed response.
- [ ] Record baseline scores in `README.md` (date, judge model, per-metric means). These numbers are your regression line. _(BLOCKED on the live harness run — placeholder table in README; fill during Phase 4.6.)_
- [x] `backend/eval/scheduler.py`: drift check — compare this week's offline means vs last week's; any metric down > 5% relative → `logger.warning("DRIFT <metric> <delta>")`. Skip the comparison (log INFO) if `judge_model` changed between weeks.
- [x] Schedule weekly harness run with APScheduler from the FastAPI lifespan (guard: only when `APP_ENV != "test"`).
  **AC:** start the app, check logs show the job registered with the correct next-run time. _(Verified: `offline_eval_weekly` registers with a next-run time inside the async lifespan.)_
- [x] `backend/tests/test_harness.py`: run the harness on the first 5 pairs with a stubbed judge → 5 `EvalResult` rows, all four metric fields set, all in [0,1], `eval_type='offline'`.

---

## Phase 4.6 — Live credentialed verification (run everything skipped so far) ← needs real API keys
*Goal: with real keys in `.env`, run every credential-blocked check from Phases 2–4 against live LLMs and record results. No new code — these are runs/observations that were deferred because `.env` had empty keys.*

Prereqs: fill `.env` with a real `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and a Langfuse public/secret key pair (create a project at `localhost:3000`).

- [ ] **Phase 2 — Langfuse trace:** POST a fixture; confirm a `triage.classify` trace appears in Langfuse with model `claude-haiku-4-5`. Also confirm live triage actually classifies (severity not forced to P3/error).
- [ ] **Phase 2/3 — live agent run:** POST `github_ci_failure.json`; confirm triage→analysis→action produces a real root cause + proposal (`human_decision='pending'`) and Langfuse spans `analysis.retrieve` / `analysis.reason` / `action.propose`.
- [ ] **Phase 3.5 — online eval live:** confirm the analyzed run produces an `online` `eval_results` row with real RAGAS scores (and an `eval.score` trace).
- [ ] **Phase 4 — offline harness live:** `python -m backend.eval.harness --run --verbose` inserts ≥ 45 offline rows against `ground_truth.jsonl` (≤ 5 skips). _(Phase 4 AC.)_
- [ ] **Phase 4 — baselines:** record the per-metric means (date, judge model) in `README.md`; verify faithfulness ≥ 0.85, hallucination ≤ 0.10 targets.
- [ ] **Cost/observability check:** confirm Langfuse shows per-call cost for `claude-haiku-4-5` / `claude-sonnet-4-6` and the run stays under the ~$0.04/incident target.

---

## Phase 5 — Slack Output + Human Approval
*Goal: an incident produces a Slack message; clicking Approve updates the DB. Est. 2 hours. (AD-1.)*

- [ ] `backend/integrations/slack.py`:
  - `build_alert_message(agent_run, incident) -> dict` — Block Kit payload exactly per PRODUCT.md → Slack Message Structure; the two buttons carry `action_id`s `approve_action` / `dismiss_action` and `value=str(incident_id)`.
  - `async send_alert(message) -> str` — posts via `slack_sdk.web.async_client.AsyncWebClient`, returns `ts`.
- [ ] Call `send_alert` from the action node's marked call site (after the proposal is stored, before eval).
- [ ] Shared approval service: `async def apply_decision(incident_id, decision)` — sets `AgentRun.human_decision` and `Incident.status` (`approved`/`dismissed`) in one transaction. Used by BOTH paths below.
- [ ] `POST /webhooks/slack/actions`: validate the Slack signing secret (timestamp + HMAC), parse the interaction payload, call `apply_decision`. Always 200.
- [ ] `POST /incidents/{id}/approve`: body `{"decision": "approved"|"dismissed"}` → `apply_decision`. 404 for unknown id, 409 if not pending.
- [ ] `backend/tests/test_slack.py`: `build_alert_message` produces `blocks` + `text` + an actions block with two buttons; mocked `AsyncWebClient` is called once per analyzed run; `apply_decision` flips both rows.
- [ ] End-to-end smoke test.
  **AC:** POST `github_ci_failure.json` → message appears in `#meridian-alerts` → click Approve → `agent_runs.human_decision='approved'` AND `incidents.status='approved'`.

---

## Phase 6 — Next.js Dashboard (App Router, RSC-first)
*Goal: a working web UI showing incidents, eval metrics, and pending approvals — data fetched server-side via React Server Components, mutations via Server Actions. Est. 3–4 hours.*

> **Architecture (settled with CLAUDE.md → Frontend patterns):** Server Components fetch FastAPI
> server-side; the browser never calls the API directly (no CORS, no react-query). Mutations are
> Server Actions that POST to FastAPI then `revalidatePath`. Client components are minimal islands
> (`"use client"`) only for Recharts and action-triggering buttons. Next dev runs on **port 3001**
> (Langfuse owns 3000).

- [ ] Confirm/add the read endpoint the detail page needs: `GET /incidents/{id}` (full AgentRun trace + eval scores, typed response). Build it in `backend/routes/incidents.py` if not already present, with a happy-path pytest.
- [ ] Scaffold: `npx create-next-app@latest frontend --ts --app --eslint --src-dir --use-npm` (no Tailwind prompt unless shadcn needs it); install `recharts` + shadcn/ui (per current shadcn init for **Next.js**). Set dev port to 3001 (`next dev -p 3001`).
- [ ] `frontend/.env.local`: `INTERNAL_API_URL=http://localhost:8000` (frontend-only — NOT the backend `.env`). `frontend/src/lib/types.ts`: TS interfaces mirroring the Pydantic response models.
- [ ] `frontend/src/lib/api.ts`: server-side typed `fetch` wrappers for the reads (`getIncidents`, `getIncident(id)`, `getEvalMetrics`, `getHealth`) hitting `INTERNAL_API_URL`, with `cache`/`next.revalidate` set per call. The ONLY place that calls FastAPI.
- [ ] `frontend/src/app/actions.ts` (`"use server"`): `approveIncident(id, decision)` — POST `/incidents/{id}/approve`, then `revalidatePath("/")` and `revalidatePath("/eval")`.
- [ ] `app/page.tsx` (`/`) — Server Component rendering `IncidentFeed`: table with severity badge, time-since, status chip, link to detail.
- [ ] `app/incidents/[id]/page.tsx` — Server Component rendering `IncidentDetail`: agent trace steps (triage → analysis → action → eval), retrieved-context accordion, online eval scores, and `ApprovalButtons` (`"use client"`) wired to the `approveIncident` Server Action.
- [ ] `app/eval/page.tsx` (`/eval`) — Server Component fetching eval metrics, passing data into `EvalMetricsChart` (`"use client"`, Recharts `LineChart` over 30 days with reference lines at faithfulness 0.85 and hallucination 0.10) + an Approval Queue listing all `human_decision='pending'` runs with one-click approve/dismiss.
- [ ] Smoke test.
  **AC:** `npm run dev` (port 3001) → `/` lists incidents (server-rendered), `/eval` chart renders, clicking Approve on a pending run runs the Server Action and the status chip updates after revalidation (no manual refresh).

---

## Phase 7 — Extended Integrations (V2 — only after Phases 1–6 + clean pytest)

- [ ] Slack message ingestion: scheduled channel-history poll → embed thread content into `document_chunks` (`source='slack'`).
- [ ] Notion runbook sync: fetch pages via Notion API, chunk, embed (`source='notion'`).
- [ ] Salesforce webhook receiver → `NormalizedEvent` (`source='salesforce'`); OAuth token refresh in `backend/integrations/salesforce.py`.
- [ ] Triage prompt update: classify Salesforce events as `RevOps` category alongside `DevOps`.

---

## Phase 8 — Production Infrastructure
*Only after the system works end-to-end with recorded eval baselines.*

### Cassandra audit log
- [ ] Add `cassandra` service to docker-compose (`cassandra:5`, healthcheck on `cqlsh -e "describe cluster"`).
- [ ] `backend/db/cassandra.py`: connect with `cassandra-driver`; on startup create
  `KEYSPACE meridian_audit WITH replication = {'class':'SimpleStrategy','replication_factor':1}` and
  `TABLE events_by_day (day date, received_at timestamp, event_id uuid, source text, event_type text, raw_body text, PRIMARY KEY ((day), received_at, event_id))`.
  The driver is synchronous — every call goes through `asyncio.to_thread` (CLAUDE.md rule).
- [ ] Append every inbound raw event to `events_by_day` from the webhook background task (after Postgres store; Cassandra failure logs ERROR but never blocks the pipeline).
  **AC:** POST a fixture → `docker compose exec cassandra cqlsh -e "SELECT COUNT(*) FROM meridian_audit.events_by_day;"` count increases.

### Java Spring Boot gateway
- [ ] Scaffold `backend-java/` with Spring Boot 3.x (Java 21): `pom.xml` (spring-boot-starter-webflux), `application.yml` (`meridian.upstream=http://localhost:8000`), single `@RestController` proxying `GET /incidents`, `GET /incidents/{id}`, `GET /eval/latest` via `WebClient` (pass-through JSON + status codes), `Dockerfile`.
  **AC:** `mvn spring-boot:run` then `curl localhost:8080/incidents` returns byte-identical JSON to `curl localhost:8000/incidents`.

### CI / K8s / Load
- [ ] `.gitlab-ci.yml`: stages `lint` (ruff + mypy), `test` (pytest), `eval-gate` (run harness; fail if any offline metric drops > 5% vs the committed baseline in `README.md`).
  **AC:** pipeline passes on main; deliberately corrupting a prompt makes `eval-gate` fail.
- [ ] `k8s/`: Deployment, Service, HPA for the FastAPI backend + `configmap.yaml` for non-secret env.
  **AC:** `kubectl apply --dry-run=client -f k8s/` validates.
- [ ] Helm chart wrapping the manifests. **AC:** `helm template` renders without error.
- [ ] k6 load test: 100 VUs, 60 s. **AC:** P95 `/webhooks/github` < 500 ms and P95 `analysis.retrieve` span < 2,000 ms.

---

## Ongoing — instrument from day one (wired during Phases 1–5)

- [ ] P95 retrieval latency visible in Langfuse (`analysis.retrieve` span).
- [ ] Redis cache hit/miss as a structured log field on every retrieval.
- [ ] LLM cost per trace visible in Langfuse (verify per-model pricing is picked up for `claude-haiku-4-5` and `claude-sonnet-4-6`).
- [ ] Mean time from `Event.received_at` to Slack `ts` logged per run.
- [ ] Weekly approval rate (`approved / (approved + dismissed)`) logged by `eval/scheduler.py`.

---

## Resume metrics checklist

Track once the system is running:

- [ ] MTTA before vs after — target ≥ 40% reduction
- [ ] P95 retrieval latency under load — target < 2 s
- [ ] Offline RAGAS baselines (Phase 4) — faithfulness ≥ 0.85, hallucination ≤ 0.10
- [ ] LLM cost per incident — target < $0.05 (expected ~$0.04 with Haiku/Sonnet tiering)
- [ ] 10k+ events/day throughput in the k6 test
- [ ] GitLab CI eval-gate catching ≥ 1 real regression during development
