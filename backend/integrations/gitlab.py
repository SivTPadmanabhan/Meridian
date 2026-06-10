"""Parse GitLab webhook payloads into a NormalizedEvent.

Supported event types (the X-Gitlab-Event header, e.g. "Pipeline Hook",
"Job Hook"): pipeline and job. Defensive parsing, same contract as github.py.
"""

from datetime import datetime, timezone

from backend.integrations.normalized import NormalizedEvent


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00").replace(" UTC", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.now(timezone.utc)


def _project(payload: dict) -> str:
    project = payload.get("project") or {}
    return (
        project.get("path_with_namespace")
        or project.get("name")
        or "unknown/unknown"
    )


def _parse_pipeline(payload: dict) -> tuple[str, str, datetime]:
    attrs = payload.get("object_attributes") or {}
    status = attrs.get("status", "unknown")
    ref = attrs.get("ref", "")
    commit = payload.get("commit") or {}
    builds = payload.get("builds") or []
    failed = [b.get("name") for b in builds if b.get("status") == "failed"]
    title = f"Pipeline {status} on {ref}" if ref else f"Pipeline {status}"
    body_parts = [commit.get("message", "")]
    if failed:
        body_parts.append("Failed jobs: " + ", ".join(n for n in failed if n))
    body = "\n".join(p for p in body_parts if p)
    return title, body, _parse_ts(attrs.get("finished_at") or attrs.get("created_at"))


def _parse_job(payload: dict) -> tuple[str, str, datetime]:
    name = payload.get("build_name", "job")
    status = payload.get("build_status", "unknown")
    stage = payload.get("build_stage", "")
    failure = payload.get("build_failure_reason", "")
    commit = payload.get("commit") or {}
    title = f"Job '{name}' ({stage}): {status}".replace(" ()", "")
    body_parts = [commit.get("message", "")]
    if failure and failure != "unknown_failure":
        body_parts.append(f"Failure reason: {failure}")
    body = "\n".join(p for p in body_parts if p)
    return title, body, _parse_ts(payload.get("build_finished_at"))


# GitLab sends a human header ("Pipeline Hook"); the object_kind field is the
# stable discriminator, so callers should pass object_kind as event_type.
_PARSERS = {
    "pipeline": _parse_pipeline,
    "build": _parse_job,   # job events use object_kind == "build"
    "job": _parse_job,
}


def parse_gitlab_event(event_type: str, payload: dict) -> NormalizedEvent:
    kind = payload.get("object_kind", event_type)
    parser = _PARSERS.get(kind)
    if parser is None:
        title = f"GitLab {kind} event"
        body = ""
        occurred_at = datetime.now(timezone.utc)
    else:
        title, body, occurred_at = parser(payload)
    return NormalizedEvent(
        source="gitlab",
        event_type=kind,
        repo=_project(payload),
        title=title,
        body_text=body,
        occurred_at=occurred_at,
        raw=payload,
    )
