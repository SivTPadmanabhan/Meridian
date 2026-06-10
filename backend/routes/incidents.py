"""Incident read endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import get_db
from backend.models.agent_run import AgentRun
from backend.models.incident import Incident

router = APIRouter(prefix="/incidents", tags=["incidents"])


class IncidentSummary(BaseModel):
    id: uuid.UUID
    title: str
    severity: str | None
    status: str
    confidence: float | None
    created_at: datetime


@router.get("", response_model=list[IncidentSummary])
async def list_incidents(db: AsyncSession = Depends(get_db)) -> list[IncidentSummary]:
    """Last 20 incidents, newest first, joined to their AgentRun."""
    stmt = (
        select(Incident, AgentRun)
        .join(AgentRun, AgentRun.incident_id == Incident.id, isouter=True)
        .order_by(Incident.created_at.desc())
        .limit(20)
    )
    rows = (await db.execute(stmt)).all()
    summaries: list[IncidentSummary] = []
    for incident, run in rows:
        triage = (run.triage_output if run else None) or {}
        summaries.append(
            IncidentSummary(
                id=incident.id,
                title=incident.title,
                severity=incident.severity,
                status=incident.status,
                confidence=triage.get("confidence"),
                created_at=incident.created_at,
            )
        )
    return summaries
