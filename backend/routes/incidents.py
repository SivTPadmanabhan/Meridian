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
from backend.models.eval_result import EvalResult
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
    human_decision: str | None
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
                human_decision=run.human_decision if run else None,
                created_at=incident.created_at,
            )
        )
    return summaries


class EvalScore(BaseModel):
    eval_type: str
    faithfulness: float | None
    response_relevancy: float | None
    hallucination_rate: float | None
    context_precision: float | None
    factual_correctness: float | None
    judge_model: str
    scored_at: datetime


class IncidentDetail(BaseModel):
    id: uuid.UUID
    title: str
    severity: str | None
    status: str
    created_at: datetime
    resolved_at: datetime | None
    triage_output: dict | None
    analysis_output: dict | None
    action_proposed: dict | None
    human_decision: str | None
    completed_at: datetime | None
    eval_scores: list[EvalScore]


@router.get("/{incident_id}", response_model=IncidentDetail)
async def get_incident(
    incident_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> IncidentDetail:
    """Full incident trace: AgentRun outputs + online eval scores (Phase 6 detail page)."""
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")

    run = (
        await db.execute(select(AgentRun).where(AgentRun.incident_id == incident_id))
    ).scalar_one_or_none()

    scores: list[EvalScore] = []
    if run is not None:
        eval_rows = (
            await db.execute(
                select(EvalResult)
                .where(EvalResult.agent_run_id == run.id)
                .order_by(EvalResult.scored_at.desc())
            )
        ).scalars().all()
        scores = [
            EvalScore(
                eval_type=r.eval_type,
                faithfulness=r.faithfulness,
                response_relevancy=r.response_relevancy,
                hallucination_rate=r.hallucination_rate,
                context_precision=r.context_precision,
                factual_correctness=r.factual_correctness,
                judge_model=r.judge_model,
                scored_at=r.scored_at,
            )
            for r in eval_rows
        ]

    return IncidentDetail(
        id=incident.id,
        title=incident.title,
        severity=incident.severity,
        status=incident.status,
        created_at=incident.created_at,
        resolved_at=incident.resolved_at,
        triage_output=run.triage_output if run else None,
        analysis_output=run.analysis_output if run else None,
        action_proposed=run.action_proposed if run else None,
        human_decision=run.human_decision if run else None,
        completed_at=run.completed_at if run else None,
        eval_scores=scores,
    )


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
