"""Build ground_truth.jsonl from real, human-authored Stack Exchange Q&A.

Source: P1ayer-1/stack-exchange-preferences-code-v2 (Stack Exchange Network
content, CC-BY-SA 4.0). Each pair is a real question with its human-accepted
answer — no AI-authored content. Attribution (the question URL) is kept per row.

Category mapping (incident_type uses the CLAUDE.md label set):
  - ci_failure         ← devops/serverfault questions about CI/CD pipelines
  - deploy_regression  ← devops/serverfault questions about deploys/rollouts
  - edge_case          ← other devops/serverfault questions (fallback bucket)
  - pr_stale           ← codereview/softwareengineering questions (PR / code review)
                         (no public "stalled PR" corpus exists; this is the
                         closest real code-review data — see PRODUCT/TODO notes)

Run:  python -m backend.eval.build_ground_truth   (writes ground_truth.jsonl)
"""

import argparse
import html
import json
import logging
import re
from pathlib import Path

from datasets import load_dataset

logger = logging.getLogger(__name__)

OUT_FILE = Path(__file__).resolve().parent / "ground_truth.jsonl"
DATASET = "P1ayer-1/stack-exchange-preferences-code-v2"

# Quotas (≥50 total; ≥15 ci, ≥15 pr, ≥10 deploy, ≥10 edge per TODO Phase 4).
QUOTAS = {"ci_failure": 16, "deploy_regression": 12, "edge_case": 12, "pr_stale": 16}
MAX_SCAN_PER_SPLIT = 6000          # cap streamed rows per split
MIN_Q_CHARS, MIN_A_CHARS = 80, 150
MAX_Q_CHARS, MAX_A_CHARS, MAX_CTX_CHARS = 700, 1400, 1000

_CI_KW = ("continuous integration", "ci/cd", "ci pipeline", "jenkins", "github actions",
          "gitlab ci", "gitlab-ci", "travis", "circleci", "teamcity", "bamboo",
          "azure pipelines", "build fail", "pipeline fail", "build server")
_DEPLOY_KW = ("deploy", "deployment", "rollout", "rollback", "release pipeline",
              "kubernetes", "helm", "terraform", "ansible", "canary", "blue-green",
              "production outage", "regression after", "cd pipeline")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def _accepted_answer(answers: list[dict]) -> str | None:
    for a in answers or []:
        if a.get("selected"):
            return strip_html(a.get("text", ""))
    return None


def _classify_ops(text: str) -> str:
    low = text.lower()
    if any(k in low for k in _CI_KW):
        return "ci_failure"
    if any(k in low for k in _DEPLOY_KW):
        return "deploy_regression"
    return "edge_case"


def _record(qid, question: str, answer: str, url: str, incident_type: str) -> dict:
    return {
        "question": question[:MAX_Q_CHARS],
        "answer": answer[:MAX_A_CHARS],
        "contexts": [question[:MAX_CTX_CHARS]],
        "incident_type": incident_type,
        "source": url,  # CC-BY-SA attribution (Stack Exchange Network)
    }


def _harvest(split: str, classify, buckets: dict, seen: set) -> None:
    ds = load_dataset(DATASET, split=split, streaming=True)
    for i, row in enumerate(ds):
        if i >= MAX_SCAN_PER_SPLIT or all(len(buckets[c]) >= QUOTAS[c] for c in classify.targets):
            break
        question = strip_html(row.get("question", ""))
        answer = _accepted_answer(row.get("answers"))
        if not answer or len(question) < MIN_Q_CHARS or len(answer) < MIN_A_CHARS:
            continue
        incident_type = classify(question)
        if incident_type not in classify.targets or len(buckets[incident_type]) >= QUOTAS[incident_type]:
            continue
        qid = row.get("qid")
        if qid in seen:
            continue
        seen.add(qid)
        meta = row.get("metadata") or []
        url = meta[0] if meta else f"{DATASET}#{qid}"
        buckets[incident_type].append(_record(qid, question, answer, url, incident_type))


def build() -> list[dict]:
    buckets: dict[str, list[dict]] = {c: [] for c in QUOTAS}
    seen: set = set()

    def ops_classify(q: str) -> str:
        return _classify_ops(q)
    ops_classify.targets = ("ci_failure", "deploy_regression", "edge_case")

    def pr_classify(q: str) -> str:
        return "pr_stale"
    pr_classify.targets = ("pr_stale",)

    for split in ("devops.stackexchange.com", "serverfault.com"):
        _harvest(split, ops_classify, buckets, seen)
    for split in ("codereview.stackexchange.com", "softwareengineering.stackexchange.com"):
        _harvest(split, pr_classify, buckets, seen)

    rows = [r for bucket in buckets.values() for r in bucket]
    for category, items in buckets.items():
        logger.info("%s: %d/%d", category, len(items), QUOTAS[category])
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Meridian ground truth from Stack Exchange")
    parser.add_argument("--out", default=str(OUT_FILE))
    args = parser.parse_args()
    logging.basicConfig(level="INFO")

    rows = build()
    out = Path(args.out)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    logger.info("wrote %d pairs to %s", len(rows), out)
    if len(rows) < 50:
        logger.warning("only %d pairs (<50) — widen MAX_SCAN_PER_SPLIT or quotas", len(rows))


if __name__ == "__main__":
    main()
