"""Eval read endpoints."""

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_db
from backend.models.eval_result import EvalResult

router = APIRouter(prefix="/eval", tags=["eval"])


class EvalDayBucket(BaseModel):
    day: date
    eval_type: str
    count: int
    faithfulness: float | None
    response_relevancy: float | None
    hallucination_rate: float | None
    context_precision: float | None
    factual_correctness: float | None


@router.get("/latest", response_model=list[EvalDayBucket])
async def latest(db: AsyncSession = Depends(get_db)) -> list[EvalDayBucket]:
    """Last 30 days of eval_results, mean of each metric per day per eval_type."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    day = func.date_trunc("day", EvalResult.scored_at)
    stmt = (
        select(
            day.label("day"),
            EvalResult.eval_type,
            func.count().label("count"),
            func.avg(EvalResult.faithfulness),
            func.avg(EvalResult.response_relevancy),
            func.avg(EvalResult.hallucination_rate),
            func.avg(EvalResult.context_precision),
            func.avg(EvalResult.factual_correctness),
        )
        .where(EvalResult.scored_at >= cutoff)
        .group_by(day, EvalResult.eval_type)
        .order_by(day.desc(), EvalResult.eval_type)
    )
    rows = (await db.execute(stmt)).all()

    def _f(value: float | None) -> float | None:
        return float(value) if value is not None else None

    return [
        EvalDayBucket(
            day=row[0].date(),
            eval_type=row[1],
            count=row[2],
            faithfulness=_f(row[3]),
            response_relevancy=_f(row[4]),
            hallucination_rate=_f(row[5]),
            context_precision=_f(row[6]),
            factual_correctness=_f(row[7]),
        )
        for row in rows
    ]
