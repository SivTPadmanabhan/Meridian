"""Offline RAGAS harness — the offline half of AD-2.

For each ground-truth pair: run the retriever + the analysis prompt path to
produce a response, then score four metrics (Faithfulness, ResponseRelevancy,
ContextPrecision, FactualCorrectness) against the reference answer with an
independent OpenAI judge (AD-6). One EvalResult(eval_type='offline') is stored
per pair. A bad pair (NaN, judge error) is logged and skipped — it never kills
the run.

CLI: python -m backend.eval.harness --run [--verbose]
"""

import argparse
import asyncio
import json
import logging
import math
import uuid
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI
from langfuse import observe
from ragas import EvaluationDataset, evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (
    ContextPrecision,
    FactualCorrectness,
    Faithfulness,
    ResponseRelevancy,
)

from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.models.eval_result import EvalResult
from backend.rag.retriever import retrieve

logger = logging.getLogger(__name__)

GROUND_TRUTH_FILE = Path(__file__).resolve().parent / "ground_truth.jsonl"

# RAGAS result column names (verified against ragas 0.4.3).
_COLUMNS = {
    "faithfulness": "faithfulness",
    "response_relevancy": "answer_relevancy",
    "context_precision": "context_precision",
    "factual_correctness": "factual_correctness",
}

_llm = None


def _get_llm() -> ChatAnthropic:
    global _llm
    if _llm is None:
        _llm = ChatAnthropic(
            model=settings.ANTHROPIC_ANALYSIS_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            max_tokens=1024,
        )
    return _llm


_SYSTEM_PROMPT = (
    "You are the analysis stage of an engineering operations assistant. "
    "Given a question about a DevOps incident and similar past incidents, answer "
    "with the most likely root cause and resolution in 2–4 sentences."
)


def load_ground_truth(path: Path = GROUND_TRUTH_FILE) -> list[dict]:
    pairs: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


async def generate_response(question: str) -> tuple[str, list[str]]:
    """Run the live system path: retrieve context, then reason a response."""
    contexts = await retrieve(question, k=5)
    context_block = "\n\n---\n\n".join(contexts) if contexts else "(no similar incidents)"
    result = await _get_llm().ainvoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Question:\n{question}\n\nContext:\n{context_block}"},
        ]
    )
    response = result.content if isinstance(result.content, str) else str(result.content)
    return response, contexts


def _num(df, row, column: str) -> float | None:
    if column not in df.columns:
        return None
    value = float(row[column])
    return None if math.isnan(value) else value


@observe(name="harness.run")
def _score(sample: dict, judge) -> dict:  # noqa: ANN001
    dataset = EvaluationDataset.from_list([sample])
    result = evaluate(
        dataset=dataset,
        metrics=[Faithfulness(), ResponseRelevancy(), ContextPrecision(), FactualCorrectness()],
        llm=judge,
    )
    df = result.to_pandas()
    row = df.iloc[0]
    return {key: _num(df, row, col) for key, col in _COLUMNS.items()}


async def _store(scores: dict) -> None:
    faithfulness = scores["faithfulness"]
    async with AsyncSessionLocal() as session:
        session.add(
            EvalResult(
                id=uuid.uuid4(),
                eval_type="offline",
                agent_run_id=None,
                faithfulness=faithfulness,
                response_relevancy=scores["response_relevancy"],
                hallucination_rate=(1.0 - faithfulness) if faithfulness is not None else None,
                context_precision=scores["context_precision"],
                factual_correctness=scores["factual_correctness"],
                judge_model=settings.OPENAI_JUDGE_MODEL,
            )
        )
        await session.commit()


async def run(pairs: list[dict] | None = None, *, store: bool = True) -> list[dict]:
    """Score every pair; skip (warn) on per-pair failure. Returns stored score dicts."""
    if pairs is None:
        pairs = load_ground_truth()
    judge = LangchainLLMWrapper(
        ChatOpenAI(model=settings.OPENAI_JUDGE_MODEL, api_key=settings.OPENAI_API_KEY)
    )
    stored: list[dict] = []
    for index, pair in enumerate(pairs):
        try:
            response, contexts = await generate_response(pair["question"])
            sample = {
                "user_input": pair["question"],
                "retrieved_contexts": contexts,
                "response": response,
                "reference": pair["answer"],
            }
            scores = await asyncio.to_thread(_score, sample, judge)
            if store:
                await _store(scores)
            stored.append(scores)
            logger.info("scored pair %d/%d: %s", index + 1, len(pairs), scores)
        except Exception:
            logger.warning("skipping pair %d (%s)", index, pair.get("incident_type"), exc_info=True)
    logger.info("offline harness complete: %d/%d pairs stored", len(stored), len(pairs))
    return stored


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Meridian offline RAGAS harness")
    parser.add_argument("--run", action="store_true", help="run the harness against ground truth")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level="DEBUG" if args.verbose else "INFO")
    if not args.run:
        parser.error("nothing to do — pass --run")
    await run()


if __name__ == "__main__":
    asyncio.run(_main())
