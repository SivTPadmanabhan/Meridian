# Meridian

Multi-agent enterprise operations intelligence platform. See `PRODUCT.md` for vision,
`CLAUDE.md` for architecture/standards, and `TODO.md` for the build checklist.

## Status

Phases 0–4 implemented (foundation, ingest, triage, analysis+action, online eval, offline
harness). The full graph runs `triage → analysis → action → eval → END`.

## Eval baselines (offline RAGAS harness)

**Not yet recorded — pending a live judged run.** The offline harness (`backend/eval/harness.py`)
needs a real `OPENAI_API_KEY` (the RAGAS judge) and `ANTHROPIC_API_KEY` (response generation) to
score the ground-truth set. Once keys are set, run:

```
python -m backend.eval.harness --run --verbose
```

then record below: **date**, **judge model** (`OPENAI_JUDGE_MODEL`), and the per-metric means
(`faithfulness`, `response_relevancy`, `context_precision`, `factual_correctness`,
`hallucination_rate`). These become the regression baseline the weekly drift check compares against.

| Date | Judge model | faithfulness | response_relevancy | context_precision | factual_correctness | hallucination_rate |
|------|-------------|--------------|--------------------|-------------------|---------------------|--------------------|
| _pending_ | _pending_ | — | — | — | — | — |

Targets (PRODUCT.md): faithfulness ≥ 0.85, hallucination ≤ 0.10.

## Ground-truth data & attribution

`backend/eval/ground_truth.jsonl` (56 pairs) is built by `backend/eval/build_ground_truth.py`
from **real, human-authored** Q&A — no AI-generated content.

**Source & license:** Stack Exchange Network content
(`P1ayer-1/stack-exchange-preferences-code-v2`), licensed **CC-BY-SA 4.0**. Sites used:
`devops.stackexchange.com`, `serverfault.com`, `codereview.stackexchange.com`,
`softwareengineering.stackexchange.com`. Each row keeps a `source` URL for attribution as required
by CC-BY-SA. The `pr_stale` label holds code-review questions (no public "stalled PR" corpus
exists — this is the closest real code-review data).

Regenerate with: `python -m backend.eval.build_ground_truth`
