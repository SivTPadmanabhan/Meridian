"""Inbound rate limiting (Phase 8 — security hardening).

A single ``slowapi`` ``Limiter`` backed by the same Redis the app already uses
(``settings.REDIS_URL``), so limits are shared across worker processes. Limits are
passed as *callables* that read ``settings`` at request time — this keeps them
runtime-configurable and lets tests tune them without re-importing the routes.

Wiring lives in ``backend/main.py`` (registers ``app.state.limiter`` + the 429
handler). Per-endpoint limits are applied with ``@limiter.limit(...)`` decorators
in the route modules.

Policy (CLAUDE.md → Security Guardrails → Rate limiting):
- Webhooks (``/webhooks/github|gitlab``): GENEROUS — never 429 a valid signed hook.
- Human endpoints (``/incidents/{id}/approve``, ``/webhooks/slack/actions``): TIGHTER.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.REDIS_URL,
    enabled=settings.RATE_LIMIT_ENABLED,
    # key_style="endpoint" scopes the limit to (client IP, view function) — NOT the
    # full URL. With the default "url", /incidents/{id}/approve buckets per incident
    # id, so a client could evade the limit by varying the id. We want a per-IP
    # cap across the whole endpoint.
    key_style="endpoint",
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return 429 + Retry-After (the window, in seconds) when a limit is exceeded.

    Custom (vs slowapi's default) so Retry-After is emitted without enabling
    response-header injection on the success path — that would require a
    ``response: Response`` parameter on every limited endpoint.
    """
    retry_after = 60
    try:
        retry_after = int(exc.limit.limit.get_expiry())  # window seconds
    except Exception:  # noqa: BLE001 — defensive: never let the handler itself fail
        pass
    response = JSONResponse(
        status_code=429, content={"detail": f"rate limit exceeded: {exc.detail}"}
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


def webhook_limit() -> str:
    """Generous per-IP limit for provider webhooks."""
    return settings.RATE_LIMIT_WEBHOOK


def approve_limit() -> str:
    """Tighter per-IP limit for the human approval endpoint."""
    return settings.RATE_LIMIT_APPROVE


def slack_limit() -> str:
    """Tighter per-IP limit for the Slack actions webhook."""
    return settings.RATE_LIMIT_SLACK
