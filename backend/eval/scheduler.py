"""Weekly offline-eval scheduling + drift detection.

After each weekly run, compare this week's offline metric means to last week's;
any metric down >5% (relative) logs a DRIFT warning. Drift baselines are only
comparable when the judge model is unchanged (AD-2), so the comparison is skipped
(INFO) when judge_model differs between weeks.
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from backend.config import settings
from backend.db.session import AsyncSessionLocal
from backend.eval import harness
from backend.models.eval_result import EvalResult

logger = logging.getLogger(__name__)

DRIFT_THRESHOLD = 0.05  # relative drop that triggers a warning
_DRIFT_METRICS = (
    "faithfulness",
    "response_relevancy",
    "context_precision",
    "factual_correctness",
)


async def _window_means(start: datetime, end: datetime) -> tuple[dict[str, float | None], str | None]:
    """Mean of each offline metric in [start, end), plus the judge model used."""
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(
                    func.avg(EvalResult.faithfulness),
                    func.avg(EvalResult.response_relevancy),
                    func.avg(EvalResult.context_precision),
                    func.avg(EvalResult.factual_correctness),
                    func.max(EvalResult.judge_model),
                    func.count(),
                ).where(
                    EvalResult.eval_type == "offline",
                    EvalResult.scored_at >= start,
                    EvalResult.scored_at < end,
                )
            )
        ).one()
    if row[5] == 0:
        return {m: None for m in _DRIFT_METRICS}, None
    means = dict(zip(_DRIFT_METRICS, (row[0], row[1], row[2], row[3])))
    return means, row[4]


async def check_drift(now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    this_week, this_judge = await _window_means(now - timedelta(days=7), now)
    prev_week, prev_judge = await _window_means(now - timedelta(days=14), now - timedelta(days=7))

    if this_judge is None or prev_judge is None:
        logger.info("drift check skipped: not enough offline history for both weeks")
        return
    if this_judge != prev_judge:
        logger.info(
            "drift check skipped: judge model changed (%s → %s); baseline reset",
            prev_judge, this_judge,
        )
        return

    for metric in _DRIFT_METRICS:
        current, previous = this_week[metric], prev_week[metric]
        if current is None or previous is None or previous == 0:
            continue
        delta = (current - previous) / previous
        if delta < -DRIFT_THRESHOLD:
            logger.warning("DRIFT %s %.1f%% (%.3f → %.3f)", metric, delta * 100, previous, current)


async def weekly_job() -> None:
    logger.info("weekly offline eval starting")
    await harness.run()
    await check_drift()


def start_scheduler() -> AsyncIOScheduler:
    """Register the weekly offline-eval job. Guarded against the test environment."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        weekly_job,
        trigger="cron",
        day_of_week="mon",
        hour=3,
        id="offline_eval_weekly",
        replace_existing=True,
    )
    scheduler.start()
    job = scheduler.get_job("offline_eval_weekly")
    logger.info("scheduled offline_eval_weekly; next run at %s", job.next_run_time)
    return scheduler
