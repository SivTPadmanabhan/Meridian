"""AgentRun model — one full LangGraph pipeline execution (V1: one per incident).

``human_decision`` is a database concern, NOT graph state (AD-1).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.db.base import Base

HUMAN_DECISIONS = ("approved", "dismissed", "pending")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id")
    )
    langfuse_trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    triage_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    analysis_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    action_proposed: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # null for triaged_low runs; otherwise pending → approved | dismissed
    human_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
