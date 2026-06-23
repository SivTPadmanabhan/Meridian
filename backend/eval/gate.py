"""CI eval-gate (Phase 8 — CI/K8s/Load).

Runs the offline RAGAS harness and FAILS (exit 1) if any metric dropped more than
5% (relative) versus the committed baseline. The machine-readable baseline lives
in ``backend/eval/baseline.json`` (mirrored in README.md for humans) — CI parses
JSON, not a markdown table, so the gate can't break on formatting.

CLI:
  python -m backend.eval.gate --run [--limit N] [--verbose]   # gate (compare)
  python -m backend.eval.gate --update-baseline [--limit N]   # record a new baseline

The gate needs ANTHROPIC_API_KEY (harness response generation) + OPENAI_API_KEY
(RAGAS judge), same as the harness.
"""

import argparse
import asyncio
import datetime
import json
import logging
from pathlib import Path

from backend.config import settings
from backend.eval import harness

logger = logging.getLogger(__name__)

BASELINE_FILE = Path(__file__).resolve().parent / "baseline.json"
GATE_THRESHOLD = 0.05  # relative drop that fails the gate
_METRICS = (
    "faithfulness",
    "response_relevancy",
    "context_precision",
    "factual_correctness",
)


def load_baseline(path: Path = BASELINE_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def means(scores: list[dict]) -> dict[str, float | None]:
    """Per-metric mean over the harness's per-pair score dicts (skips None)."""
    out: dict[str, float | None] = {}
    for metric in _METRICS:
        vals = [s[metric] for s in scores if s.get(metric) is not None]
        out[metric] = sum(vals) / len(vals) if vals else None
    return out


def compare(
    current: dict[str, float | None],
    baseline: dict,
    threshold: float = GATE_THRESHOLD,
) -> list[str]:
    """Return a list of human-readable regression messages (empty == pass)."""
    base_metrics = baseline.get("metrics", {})
    failures: list[str] = []
    for metric in _METRICS:
        cur = current.get(metric)
        base = base_metrics.get(metric)
        if cur is None or base is None or base == 0:
            continue
        delta = (cur - base) / base
        if delta < -threshold:
            failures.append(f"{metric} {delta * 100:.1f}% ({base:.3f} → {cur:.3f})")
    return failures


def _check_judge(baseline: dict) -> None:
    base_judge = baseline.get("judge_model")
    if base_judge and base_judge != settings.OPENAI_JUDGE_MODEL:
        raise SystemExit(
            f"eval-gate: baseline judge model '{base_judge}' != current "
            f"'{settings.OPENAI_JUDGE_MODEL}'. Reset the baseline (--update-baseline)."
        )


async def run_gate(limit: int | None = None, threshold: float = GATE_THRESHOLD) -> int:
    baseline = load_baseline()
    _check_judge(baseline)
    pairs = harness.load_ground_truth()
    if limit:
        pairs = pairs[:limit]
    scores = await harness.run(pairs=pairs, store=False)
    current = means(scores)
    failures = compare(current, baseline, threshold)
    logger.info("eval-gate current means: %s", current)
    if failures:
        for f in failures:
            logger.error("eval-gate REGRESSION: %s", f)
        return 1
    logger.info("eval-gate PASS (no metric dropped >%.0f%%)", threshold * 100)
    return 0


async def update_baseline(limit: int | None = None) -> dict:
    pairs = harness.load_ground_truth()
    if limit:
        pairs = pairs[:limit]
    scores = await harness.run(pairs=pairs, store=False)
    current = means(scores)
    data = {
        "date": datetime.date.today().isoformat(),
        "judge_model": settings.OPENAI_JUDGE_MODEL,
        "pairs": len(scores),
        "metrics": current,
    }
    BASELINE_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote baseline %s", data)
    return data


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Meridian offline eval-gate")
    parser.add_argument("--run", action="store_true", help="compare against baseline; exit 1 on regression")
    parser.add_argument("--update-baseline", action="store_true", help="record a fresh baseline")
    parser.add_argument("--limit", type=int, default=None, help="score only the first N pairs")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level="DEBUG" if args.verbose else "INFO")

    if args.update_baseline:
        await update_baseline(args.limit)
        return
    if args.run:
        raise SystemExit(await run_gate(args.limit))
    parser.error("nothing to do — pass --run or --update-baseline")


if __name__ == "__main__":
    asyncio.run(_main())
