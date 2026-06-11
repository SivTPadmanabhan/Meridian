"""Incident read endpoints + the shared approval service (AD-1)."""

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.session import AsyncSessionLocal, get_db
from backend.models.agent_run import AgentRun
from backend.models.incident import Incident

router = APIRouter(prefix="/incidents", tags=["incidents"])


class IncidentNotFound(Exception):
    """No incident (or its AgentRun) matches the given id."""


class NotPending(Exception):
    """The incident's AgentRun is not awaiting a decision."""

    def __init__(self, current: str | None) -> None:
        self.current = current
        super().__init__(f"current human_decision: {current}")


async def apply_decision(incident_id: uuid.UUID, decision: Literal["approved", "dismissed"]) -> None:
    """Resolve a pending proposal in one transaction (AD-1).

    Shared by both approval paths: ``POST /incidents/{id}/approve`` and the Slack
    actions webhook. Sets ``AgentRun.human_decision`` and ``Incident.status``.
    Raises ``IncidentNotFound`` / ``NotPending`` for callers to map to a response.
    """
    if decision not in ("approved", "dismissed"):
        raise ValueError(f"invalid decision: {decision}")
    async with AsyncSessionLocal() as session:
        run = (
            await session.execute(
                select(AgentRun).where(AgentRun.incident_id == incident_id)
            )
        ).scalar_one_or_none()
        incident = await session.get(Incident, incident_id)
        if run is None or incident is None:
            raise IncidentNotFound(str(incident_id))
        if run.human_decision != "pending":
            raise NotPending(run.human_decision)
        run.human_decision = decision
        incident.status = decision
        await session.commit()


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


class ApprovalRequest(BaseModel):
    decision: Literal["approved", "dismissed"]


class ApprovalResponse(BaseModel):
    incident_id: uuid.UUID
    status: str
    human_decision: str


@router.post("/{incident_id}/approve", response_model=ApprovalResponse)
async def approve_incident(incident_id: uuid.UUID, body: ApprovalRequest) -> ApprovalResponse:
    """Approve or dismiss a pending proposal (shared service with the Slack path)."""
    try:
        await apply_decision(incident_id, body.decision)
    except IncidentNotFound:
        raise HTTPException(status_code=404, detail="incident not found")
    except NotPending as exc:
        raise HTTPException(status_code=409, detail=f"incident not pending ({exc.current})")
    return ApprovalResponse(
        incident_id=incident_id, status=body.decision, human_decision=body.decision
    )
