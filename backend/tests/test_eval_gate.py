"""Phase 8 — eval-gate comparison logic (hermetic; no LLM/keys).

The live gate (running the harness vs the committed baseline) is credential-gated
like the rest of Phase 4.6; here we lock down the pure comparison: a >5% relative
drop in any metric is a regression, within-threshold is a pass.
"""

from backend.eval.gate import compare, means

_BASELINE = {
    "judge_model": "gpt-5.4-mini",
    "metrics": {
        "faithfulness": 0.90,
        "response_relevancy": 0.85,
        "context_precision": 0.80,
        "factual_correctness": 0.75,
    },
}


def test_within_threshold_passes():
    # Small drops (<5%) and any improvement must not trip the gate.
    current = {
        "faithfulness": 0.89,        # -1.1%
        "response_relevancy": 0.95,  # improved
        "context_precision": 0.78,   # -2.5%
        "factual_correctness": 0.75,
    }
    assert compare(current, _BASELINE) == []


def test_regression_beyond_threshold_flagged():
    current = dict(_BASELINE["metrics"])
    current["faithfulness"] = 0.80  # -11.1% vs 0.90 → regression
    failures = compare(current, _BASELINE)
    assert len(failures) == 1
    assert "faithfulness" in failures[0]


def test_missing_metric_is_skipped_not_failed():
    current = {"faithfulness": None, "response_relevancy": 0.85,
               "context_precision": 0.80, "factual_correctness": 0.75}
    assert compare(current, _BASELINE) == []


def test_means_skips_none_and_averages():
    scores = [
        {"faithfulness": 0.8, "response_relevancy": 0.9, "context_precision": 0.7, "factual_correctness": 0.6},
        {"faithfulness": 1.0, "response_relevancy": None, "context_precision": 0.9, "factual_correctness": 0.8},
    ]
    m = means(scores)
    assert m["faithfulness"] == 0.9            # (0.8+1.0)/2
    assert m["response_relevancy"] == 0.9      # only the non-None value
