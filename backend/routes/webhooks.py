"""Webhook ingest endpoints.

Contract (AD-7): validate signature → store the raw Event → schedule background
processing → return 200 fast. No LLM/graph work happens inline. Invalid
signatures return 401 and store nothing.

Phase 1 background processing = normalize + ingest into document_chunks. Phase 2
extends ``process_event`` to also create the Incident + AgentRun and invoke the
graph.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request

from backend.config import settings
from backend.agents.graph import get_graph
from backend.db.session import AsyncSessionLocal
from backend.integrations.github import parse_github_event
from backend.integrations.gitlab import parse_gitlab_event
from backend.integrations.normalized import NormalizedEvent
from backend.integrations.slack import (
    APPROVE_ACTION_ID,
    DISMISS_ACTION_ID,
    verify_slack_signature,
)
from backend.models.agent_run import AgentRun
from backend.models.event import Event
from backend.models.incident import Incident
from backend.rag.ingest import ingest_event
from backend.routes.incidents import IncidentNotFound, NotPending, apply_decision

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


async def verify_github_signature(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
) -> bytes:
    """Validate the GitHub HMAC-SHA256 signature; return the raw body on success."""
    body = await request.body()
    if not x_hub_signature_256:
        raise HTTPException(status_code=401, detail="missing signature")
    expected = "sha256=" + hmac.new(
        settings.GITHUB_WEBHOOK_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")
    return body


async def verify_gitlab_token(
    x_gitlab_token: str | None = Header(default=None),
) -> None:
    """Validate the GitLab shared-secret token (constant-time equality)."""
    if not x_gitlab_token or not hmac.compare_digest(
        x_gitlab_token, settings.GITLAB_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=401, detail="invalid token")


async def process_event(event_id: uuid.UUID, normalized: NormalizedEvent) -> None:
    """Background processing for an ingested event (AD-4, AD-7).

    Ingest the event into the RAG knowledge base, create exactly one Incident +
    one AgentRun, run the graph, then persist the triage outcome.
    """
    try:
        await ingest_event(normalized)
    except Exception:
        logger.exception("RAG ingest failed for event %s", event_id)

    try:
        await _run_pipeline(event_id, normalized)
    except Exception:
        logger.exception("agent pipeline failed for event %s", event_id)


async def _run_pipeline(event_id: uuid.UUID, normalized: NormalizedEvent) -> None:
    # One event = one Incident = one AgentRun (AD-4).
    async with AsyncSessionLocal() as session:
        incident = Incident(
            id=uuid.uuid4(),
            event_id=event_id,
            status="open",
            title=normalized.title[:500] or f"{normalized.source} {normalized.event_type}",
        )
        session.add(incident)
        await session.flush()  # insert incident before its FK-dependent agent_run
        run = AgentRun(id=uuid.uuid4(), incident_id=incident.id)
        session.add(run)
        await session.commit()
        incident_id = incident.id
        run_id = run.id

    initial_state = {
        "event_id": str(event_id),
        "incident_id": str(incident_id),
        "event_payload": normalized.model_dump(mode="json"),
        "severity": "",
        "confidence": 0.0,
        "retrieved_context": [],
        "root_cause": "",
        "suggested_action": "",
        "eval_scores": {},
        "error": None,
    }
    final_state = await get_graph().ainvoke(
        initial_state, config={"configurable": {"thread_id": str(incident_id)}}
    )

    severity = final_state.get("severity") or None
    confidence = final_state.get("confidence")
    errored = final_state.get("error") is not None
    # Confident low-severity runs end at triage (AD-3); human_decision stays NULL.
    triaged_low = (
        not errored
        and severity in ("P2", "P3")
        and (confidence or 0.0) >= settings.TRIAGE_CONFIDENCE_ESCALATION
    )

    async with AsyncSessionLocal() as session:
        db_run = await session.get(AgentRun, run_id)
        db_incident = await session.get(Incident, incident_id)
        db_run.triage_output = {"severity": severity, "confidence": confidence}
        db_run.completed_at = datetime.now(timezone.utc)
        db_incident.severity = severity
        if triaged_low:
            db_incident.status = "triaged_low"
        await session.commit()


async def _store_event(source: str, event_type: str, payload: dict, raw_body: bytes) -> Event:
    event = Event(
        id=uuid.uuid4(),
        source=source,
        event_type=event_type,
        payload=payload,
        raw_body=raw_body.decode("utf-8", errors="replace"),
    )
    async with AsyncSessionLocal() as session:
        session.add(event)
        await session.commit()
        await session.refresh(event)
    return event


@router.post("/github")
async def github_webhook(
    background_tasks: BackgroundTasks,
    body: bytes = Depends(verify_github_signature),
    x_github_event: str = Header(default="unknown"),
) -> dict:
    payload = await _json(body)
    event = await _store_event("github", x_github_event, payload, body)
    normalized = parse_github_event(x_github_event, payload)
    background_tasks.add_task(process_event, event.id, normalized)
    return {"status": "accepted", "event_id": str(event.id)}


@router.post("/gitlab", dependencies=[Depends(verify_gitlab_token)])
async def gitlab_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitlab_event: str = Header(default="unknown"),
) -> dict:
    body = await request.body()
    payload = await _json(body)
    normalized = parse_gitlab_event(x_gitlab_event, payload)
    event = await _store_event("gitlab", normalized.event_type, payload, body)
    background_tasks.add_task(process_event, event.id, normalized)
    return {"status": "accepted", "event_id": str(event.id)}


_SLACK_DECISIONS = {APPROVE_ACTION_ID: "approved", DISMISS_ACTION_ID: "dismissed"}


@router.post("/slack/actions")
async def slack_actions(request: Request) -> dict:
    """Handle Slack approve/dismiss button clicks (AD-1). Always 200 once signed."""
    body = await request.body()
    if not verify_slack_signature(
        body,
        request.headers.get("X-Slack-Request-Timestamp"),
        request.headers.get("X-Slack-Signature"),
    ):
        raise HTTPException(status_code=401, detail="invalid slack signature")

    payload_raw = parse_qs(body.decode("utf-8")).get("payload", [None])[0]
    if not payload_raw:
        return {"status": "ignored"}
    actions = (json.loads(payload_raw).get("actions")) or []
    if not actions:
        return {"status": "ignored"}

    action = actions[0]
    decision = _SLACK_DECISIONS.get(action.get("action_id"))
    incident_id = action.get("value")
    if decision and incident_id:
        try:
            await apply_decision(uuid.UUID(incident_id), decision)  # type: ignore[arg-type]
        except (IncidentNotFound, NotPending):
            logger.warning("slack action on non-actionable incident %s", incident_id)
        except Exception:
            logger.exception("slack action handling failed for incident %s", incident_id)
    return {"status": "ok"}


async def _json(body: bytes) -> dict:
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
