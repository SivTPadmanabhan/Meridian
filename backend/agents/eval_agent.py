"""Online eval node — scores every analyzed run (online half of AD-2).

Builds a one-row RAGAS dataset from graph state and scores Faithfulness +
ResponseRelevancy with an independent OpenAI judge (AD-6: never judge Claude with
Claude). Stores an EvalResult(eval_type='online'). Failure policy: catch
Exception, logger.warning, return {"eval_scores": {}} — never crash the pipeline.
"""

import asyncio
import logging
import math
import uuid

from langchain_openai import ChatOpenAI
from langfuse import observe
from ragas import EvaluationDataset, evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import Faithfulness, ResponseRelevancy
from sqlalchemy import select

from backend.config import settings
from backend.agents.state import MeridianState
from backend.db.session import AsyncSessionLocal
from backend.models.agent_run import AgentRun
from backend.models.eval_result import EvalResult

logger = logging.getLogger(__name__)

# RAGAS column names: Faithfulness → "faithfulness", ResponseRelevancy → "answer_relevancy".
_RELEVANCY_COLUMN = "answer_relevancy"


def _event_summary(payload: dict) -> str:
    return f"{payload.get('title', '')}\n\n{payload.get('body_text', '')}".strip()


def _num(df, row, column: str) -> float | None:
    if column not in df.columns:
        return None
    value = float(row[column])
    return None if math.isnan(value) else value


@observe(name="eval.score")
def _score(user_input: str, contexts: list[str], response: str, judge) -> dict:  # noqa: ANN001
    dataset = EvaluationDataset.from_list(
        [{"user_input": user_input, "retrieved_contexts": contexts, "response": response}]
    )
    result = evaluate(
        dataset=dataset, metrics=[Faithfulness(), ResponseRelevancy()], llm=judge
    )
    df = result.to_pandas()
    row = df.iloc[0]
    return {
        "faithfulness": _num(df, row, "faithfulness"),
        "response_relevancy": _num(df, row, _RELEVANCY_COLUMN),
    }


async def _store(incident_id: str, scores: dict) -> None:
    async with AsyncSessionLocal() as session:
        run = (
            await session.execute(
                select(AgentRun).where(AgentRun.incident_id == uuid.UUID(incident_id))
            )
        ).scalar_one()
        session.add(
            EvalResult(
                id=uuid.uuid4(),
                eval_type="online",
                agent_run_id=run.id,
                faithfulness=scores["faithfulness"],
                response_relevancy=scores["response_relevancy"],
                hallucination_rate=scores["hallucination_rate"],
                judge_model=settings.OPENAI_JUDGE_MODEL,
            )
        )
        await session.commit()


async def eval_node(state: MeridianState) -> dict:
    try:
        if not state.get("root_cause"):
            return {"eval_scores": {}}
        judge = LangchainLLMWrapper(
            ChatOpenAI(
                model=settings.OPENAI_JUDGE_MODEL,
                api_key=settings.OPENAI_API_KEY,
                max_retries=settings.LLM_MAX_RETRIES,  # backoff on 429/5xx
            )
        )
        scores = await asyncio.to_thread(
            _score,
            _event_summary(state["event_payload"]),
            state["retrieved_context"],
            state["root_cause"],
            judge,
        )
        faithfulness = scores["faithfulness"]
        # hallucination_rate is defined as 1 − faithfulness (stored explicitly).
        scores["hallucination_rate"] = (
            1.0 - faithfulness if faithfulness is not None else None
        )
        await _store(state["incident_id"], scores)
        return {"eval_scores": scores}
    except Exception:
        logger.warning(
            "eval_node failed for incident %s; skipping scores",
            state.get("incident_id"),
            exc_info=True,
        )
        return {"eval_scores": {}}
