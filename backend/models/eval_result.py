"""EvalResult model — one row per scored run (online) or ground-truth pair (offline).

Two eval systems share this table, distinguished by ``eval_type`` (AD-2).
``context_precision`` / ``factual_correctness`` are offline-only (NULL for online).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base

EVAL_TYPES = ("online", "offline")


class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    eval_type: Mapped[str] = mapped_column(String(16))  # online | offline
    # NULL for offline rows (no live AgentRun)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id"), nullable=True
    )
    faithfulness: Mapped[float | None] = mapped_column(Float, nullable=True)
    response_relevancy: Mapped[float | None] = mapped_column(Float, nullable=True)
    # stored explicitly as 1 - faithfulness so dashboards never recompute it
    hallucination_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # offline-only metrics
    context_precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    factual_correctness: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_model: Mapped[str] = mapped_column(String(64))
    scored_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
