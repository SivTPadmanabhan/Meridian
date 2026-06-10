"""Parse GitHub webhook payloads into a NormalizedEvent.

Supported event types (the X-GitHub-Event header value): push, pull_request,
check_run. Parsing is defensive — missing fields fall back to sensible defaults
rather than raising, so a slightly unexpected payload never 500s the webhook.
"""

from datetime import datetime, timezone

from backend.integrations.normalized import NormalizedEvent


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _repo(payload: dict) -> str:
    repo = payload.get("repository") or {}
    return repo.get("full_name") or repo.get("name") or "unknown/unknown"


def _parse_push(payload: dict) -> tuple[str, str, datetime]:
    ref = payload.get("ref", "")
    branch = ref.rsplit("/", 1)[-1] if ref else "unknown"
    pusher = (payload.get("pusher") or {}).get("name", "unknown")
    commits = payload.get("commits") or []
    messages = [c.get("message", "") for c in commits]
    head = payload.get("head_commit") or {}
    title = f"Push to {branch} by {pusher} ({len(commits)} commit(s))"
    body = "\n".join(m for m in messages if m) or head.get("message", "")
    return title, body, _parse_ts(head.get("timestamp"))


def _parse_pull_request(payload: dict) -> tuple[str, str, datetime]:
    action = payload.get("action", "updated")
    pr = payload.get("pull_request") or {}
    number = pr.get("number", payload.get("number", "?"))
    title = f"PR #{number} {action}: {pr.get('title', '')}".strip()
    body = pr.get("body") or ""
    return title, body, _parse_ts(pr.get("updated_at") or pr.get("created_at"))


def _parse_check_run(payload: dict) -> tuple[str, str, datetime]:
    check = payload.get("check_run") or {}
    name = check.get("name", "check")
    status = check.get("status", "")
    conclusion = check.get("conclusion") or status or "unknown"
    output = check.get("output") or {}
    summary = output.get("summary") or output.get("title") or ""
    title = f"Check run '{name}': {conclusion}"
    return title, summary, _parse_ts(check.get("completed_at") or check.get("started_at"))


_PARSERS = {
    "push": _parse_push,
    "pull_request": _parse_pull_request,
    "check_run": _parse_check_run,
}


def parse_github_event(event_type: str, payload: dict) -> NormalizedEvent:
    parser = _PARSERS.get(event_type)
    if parser is None:
        title = f"GitHub {event_type} event"
        body = ""
        occurred_at = datetime.now(timezone.utc)
    else:
        title, body, occurred_at = parser(payload)
    return NormalizedEvent(
        source="github",
        event_type=event_type,
        repo=_repo(payload),
        title=title,
        body_text=body,
        occurred_at=occurred_at,
        raw=payload,
    )
