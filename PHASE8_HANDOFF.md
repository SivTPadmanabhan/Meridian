# Phase 8 — Session Handoff

_Last updated: 2026-06-20. Reference doc for resuming Phase 8 in a new session._
_Source of truth for task status is `TODO.md` (Phase 8 section); this file adds session context, decisions, and gotchas._

---

## TL;DR — where we are

- **Phase 8 → Security hardening: ✅ COMPLETE** (all 6 items, verified for real).
- **Phase 8 → CI/K8s/Load: 🔄 IN PROGRESS** (1 of 4 items partially done).
- **Cassandra audit log + Java gateway:** already done before this session.
- Full pytest suite: **68 passing** (64 before this section + 4 new eval-gate tests). Run with the venv (below).

---

## Environment & toolchain (all confirmed installed this session)

| Tool | Status | Notes |
|------|--------|-------|
| Python venv | `C:\Users\Teju\Meridian\.venv` (3.13.1) | **Use this**, NOT bash's `python` (which is global 3.14). Call `.venv/Scripts/python.exe`. |
| Docker Desktop | running | Postgres + Redis + Langfuse stack up. Tests need Postgres+Redis healthy. |
| ruff 0.15.16, mypy 2.1.0 | ✅ | lint stage tooling |
| kubectl v1.34.1 | ✅ | for `kubectl apply --dry-run=client` |
| helm v4.2.0 | ✅ | for `helm template` |
| k6 v2.0.0 | ✅ | at `C:\Program Files\k6\k6.exe`. **NOT on PATH in existing shells** (winget updated machine PATH) — call by full path or open a new shell. |
| detect-secrets, pip-audit, pre-commit, slowapi, tenacity | ✅ (in venv) | |

### Credentials (`.env`)
- `OPENAI_API_KEY` ✅ present
- `ANTHROPIC_API_KEY` ❌ **user was adding this** — needed for eval-gate live + k6 analysis span
- `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` ❌ **user was adding these** (create a project at http://localhost:3000) — needed to measure the `analysis.retrieve` P95 span
- **CONFIRM these are set before the credentialed steps below.** Check with:
  `.venv/Scripts/python.exe -c "from backend.config import settings as s; print(bool(s.ANTHROPIC_API_KEY), bool(s.LANGFUSE_PUBLIC_KEY))"`

---

## Decisions locked this session

1. **Order:** doing Security hardening BEFORE CI/K8s/Load (user choice; deviates from TODO's in-section order).
2. **Verify for real:** install toolchain + observe every AC live where possible (memory: `phase8-verify-for-real`).
3. **Tooling install:** prefer pip-into-`.venv` over system/winget (memory: `prefer-venv-pip-tools`). Exception: kubectl/helm/k6 have no pip package → system installs (k6 via winget, approved).
4. **Secret scanning:** chose `detect-secrets` (pip) over `gitleaks` (winget).
5. **Dep CVEs:** 3 fix-less CVEs (torch/ragas/diskcache) accepted via documented `--ignore-vuln` + `SECURITY.md` (judged dormant/local-only; no product impact). Gate stays a live tripwire for new/fixable vulns.
6. **eval-gate baseline:** machine-readable `backend/eval/baseline.json` is the source the gate reads; mirror the numbers in `README.md` for humans. (Minor deviation from the AC's literal "baseline in README.md" — parsing a markdown table in CI is brittle. Flag to user; consider a sync test.)

---

## ✅ Security hardening — DONE (6/6, all verified)

1. **Secret scanning** — `.pre-commit-config.yaml` + `.secrets.baseline` (detect-secrets); `secret_scan` CI stage. Verified: fake `sk-ant-` key blocked (exit 1), clean passes.
2. **Dependency audits** — `pip-audit` + `npm audit --audit-level=high` as the `security` CI stage; 3 documented `--ignore-vuln`. Verified: fails on vulns, passes with ignores; npm high-gate green. See `SECURITY.md`.
3. **Inbound rate limiting** — `backend/ratelimit.py` (slowapi + Redis), custom 429+Retry-After handler. **Fixed real bug:** `key_style="endpoint"` (slowapi default `"url"` let clients evade the cap by varying the incident id). Verified via `test_ratelimit.py`.
4. **Outbound LLM throttle** — `ainvoke_graph()` in `backend/agents/graph.py` wraps invocation in a per-loop `asyncio.Semaphore(LLM_MAX_CONCURRENCY=8)`; `max_retries=LLM_MAX_RETRIES=3` on all LLM clients. Verified via `test_llm_throttle.py` (50 concurrent saturate to cap, never exceed).
5. **Request-size/timeout** — `_enforce_body_size` (413) on all webhook paths > `MAX_WEBHOOK_BODY_BYTES` (1 MiB); `SERVER_KEEPALIVE_TIMEOUT=15` → uvicorn. Verified via `test_request_limits.py`.
6. **Container hardening** — `Dockerfile` (non-root `appuser` uid 10001) + `.dockerignore` + compose `app` service. Verified: `docker compose run --rm app whoami` → `appuser`, `id` → uid=10001.

---

## 🔄 CI/K8s/Load — REMAINING WORK (do in this order)

### Item 1: `.gitlab-ci.yml` lint/test/eval-gate stages — PARTIALLY DONE
**Done:**
- `backend/eval/gate.py` written — runs harness vs `baseline.json`, fails on >5% relative drop. CLI: `--run` (gate) / `--update-baseline` / `--limit N`.
- `backend/tests/test_eval_gate.py` — 4 hermetic tests for compare/means logic. **Passing.**
- `ruff check backend` → **clean** (fixed an unused import in test_ratelimit.py).

**TODO:**
- [ ] **Run `mypy backend`** (this was the very next step when interrupted) — `.venv/Scripts/mypy.exe backend`. Fix any errors so the lint stage can pass (AC = "pipeline passes on main"). `pyproject.toml` sets `disallow_untyped_defs=true`. **Unknown if it's currently clean — check first.**
- [ ] Add `lint`, `test`, `eval-gate` jobs + uncomment those stages in `.gitlab-ci.yml`.
  - `lint`: `ruff check backend` + `mypy backend`.
  - `test`: needs `services:` pgvector + redis, `alembic upgrade head`, then `pytest -q`. Env: `DATABASE_URL`, `REDIS_URL`, `APP_ENV=test`.
  - `eval-gate`: services + `alembic upgrade head` + `python -m backend.rag.ingest --seed` + `python -m backend.eval.gate --run`; `rules:` gate on `$ANTHROPIC_API_KEY && $OPENAI_API_KEY` CI vars.
- [ ] **(needs keys)** Record baseline: `.venv/Scripts/python.exe -m backend.eval.gate --update-baseline` (writes `baseline.json`); mirror numbers into `README.md` table (currently `_pending_`). Targets: faithfulness ≥ 0.85, hallucination ≤ 0.10. NOTE: full harness is 56 pairs × LLM calls — consider `--limit` for a cheaper demo run, but a real baseline should use all pairs.
- [ ] **(needs keys)** Demo the AC: corrupt a prompt (e.g. in `analysis.py`/`harness.py` system prompt) → `python -m backend.eval.gate --run` exits 1 → revert.
- **AC verification caveat:** no GitLab runner locally (`gitlab-runner exec` removed in v16+). Verify each stage's command locally; full pipeline run is the one genuinely-deferred bit.

### Item 2: `k8s/` manifests — NOT STARTED
- [ ] Create Deployment, Service, HPA for the FastAPI backend. `k8s/configmap.yaml` already exists (non-secret env).
  - Deployment image: `meridian-app:latest` (built this session; dry-run won't pull). Non-root already in image. Add resource requests/limits (HPA needs CPU requests).
  - HPA: target CPU util (e.g. 70%), min/max replicas.
- [ ] **AC:** `kubectl apply --dry-run=client -f k8s/` validates (kubectl installed — verify for real).

### Item 3: Helm chart — NOT STARTED
- [ ] Wrap the k8s manifests in a chart (`Chart.yaml`, `values.yaml`, `templates/`).
- [ ] **AC:** `helm template <chart>` renders without error (helm v4 installed — verify for real).

### Item 4: k6 load test — NOT STARTED
- [ ] Write a k6 script: 100 VUs, 60s, hitting `POST /webhooks/github`.
  - **Gotcha:** webhook needs a valid `X-Hub-Signature-256` HMAC. Either compute HMAC in the k6 script (crypto module) using `GITHUB_WEBHOOK_SECRET`, or pre-generate a signed body. The app must be running (`uvicorn backend.main:app --port 8000`).
  - **Gotcha:** the `analysis.retrieve` span only fires for P0/P1 events that reach the analysis node, which needs `ANTHROPIC_API_KEY` (triage classification) — and measuring its P95 needs Langfuse. So the analysis-span half is credential-gated.
- [ ] **AC:** P95 `/webhooks/github` < 500 ms AND P95 `analysis.retrieve` span < 2000 ms.
  - Run k6 via `C:\Program Files\k6\k6.exe run <script>.js`.

---

## Files created/modified this session

**Created:** `.pre-commit-config.yaml`, `.secrets.baseline`, `.gitlab-ci.yml`, `SECURITY.md`, `Dockerfile`, `.dockerignore`, `backend/ratelimit.py`, `backend/eval/gate.py`, `backend/tests/test_ratelimit.py`, `backend/tests/test_llm_throttle.py`, `backend/tests/test_request_limits.py`, `backend/tests/test_eval_gate.py`, this file.

**Modified:** `backend/config.py` (rate-limit / LLM-throttle / request-size knobs), `backend/main.py` (limiter + 429 handler), `backend/routes/webhooks.py` (limiter decorators, body-size guard, `ainvoke_graph`), `backend/routes/incidents.py` (approve limiter), `backend/agents/graph.py` (semaphore + `ainvoke_graph`), `backend/agents/{triage,analysis,action,eval_agent}.py` + `backend/eval/harness.py` (`max_retries`), `docker-compose.yml` (`app` service), `TODO.md` (6 security items checked off).

---

## How to resume

1. Confirm Docker stack is up (`docker ps`) and keys are in `.env` (see Credentials above).
2. Read `TODO.md` → Phase 8 → "CI / K8s / Load" + "this file".
3. Start at **Item 1 → run `mypy backend`**, then proceed in order.
4. Quick green check: `.venv/Scripts/python.exe -m pytest -q` (needs Docker Postgres+Redis).
