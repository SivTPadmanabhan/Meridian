"""Application configuration.

This module is the ONLY place in the codebase that reads environment variables.
Everything else imports the ``settings`` singleton. No ``os.environ`` elsewhere.
"""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed settings loaded from the environment / ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # LLM providers
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_TRIAGE_MODEL: str = "claude-haiku-4-5"
    ANTHROPIC_ANALYSIS_MODEL: str = "claude-sonnet-4-6"
    OPENAI_API_KEY: str = ""
    OPENAI_JUDGE_MODEL: str = "gpt-5.4-mini"

    # Agent behavior
    TRIAGE_CONFIDENCE_ESCALATION: float = 0.6

    # Outbound LLM throttling (Phase 8). The semaphore bounds how many graph runs
    # execute concurrently (wired at graph invocation, not per-node) so a webhook
    # burst can't fan out into hundreds of simultaneous Anthropic/OpenAI calls.
    # max_retries gives each LLM client exponential backoff on provider 429/5xx.
    LLM_MAX_CONCURRENCY: int = 8
    LLM_MAX_RETRIES: int = 3

    # Embeddings
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIM: int = 384

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://meridian:password@localhost:5432/meridian"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_TTL: int = 600

    # Inbound rate limiting (Phase 8, slowapi backed by Redis above).
    # Webhooks are GENEROUS: a 429 makes GitHub/GitLab disable the hook, and the
    # signature is the real authenticity gate (AD-7) — never 429 a valid signed
    # webhook under normal load. Human endpoints are TIGHTER (credential-stuffing
    # / abuse surface) and return 429 + Retry-After when exceeded.
    RATE_LIMIT_WEBHOOK: str = "600/minute"
    RATE_LIMIT_APPROVE: str = "20/minute"
    RATE_LIMIT_SLACK: str = "60/minute"
    RATE_LIMIT_ENABLED: bool = True

    # Request-size & timeout limits (Phase 8). Reject oversized webhook bodies
    # (413) before doing any signature/parse work, so a huge payload can't exhaust
    # memory/CPU. 1 MiB is comfortably above real GitHub/GitLab/Slack payloads.
    # SERVER_KEEPALIVE_TIMEOUT is passed to uvicorn (see Dockerfile/run command) so
    # a slow client can't hold a connection open indefinitely.
    MAX_WEBHOOK_BODY_BYTES: int = 1_048_576
    SERVER_KEEPALIVE_TIMEOUT: int = 15

    # Langfuse (self-hosted v3)
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_HOST: str = "http://localhost:3000"

    # Slack
    SLACK_BOT_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_CHANNEL_ID: str = ""           # #meridian-alerts (outbound)
    SLACK_INGEST_CHANNEL_ID: str = ""    # channel polled into the RAG store (V2)
    SLACK_INGEST_LIMIT: int = 100        # messages fetched per poll

    # GitHub / GitLab
    GITHUB_WEBHOOK_SECRET: str = ""
    GITLAB_WEBHOOK_SECRET: str = ""

    # Notion runbook sync (V2)
    NOTION_API_KEY: str = ""
    NOTION_DATABASE_ID: str = ""
    NOTION_VERSION: str = "2022-06-28"

    # Salesforce (V2 — RevOps webhook + OAuth refresh)
    SALESFORCE_WEBHOOK_SECRET: str = ""
    SALESFORCE_CLIENT_ID: str = ""
    SALESFORCE_CLIENT_SECRET: str = ""
    SALESFORCE_REFRESH_TOKEN: str = ""
    SALESFORCE_TOKEN_URL: str = "https://login.salesforce.com/services/oauth2/token"

    # Cassandra audit log (Phase 8). Driver is synchronous — see backend/db/cassandra.py.
    # Disabled by default so dev/test never reach for a cluster that isn't running
    # (degrade-without-infra). Set true in production where Cassandra is deployed.
    CASSANDRA_AUDIT_ENABLED: bool = False
    CASSANDRA_HOSTS: list[str] = ["localhost"]
    CASSANDRA_PORT: int = 9042
    CASSANDRA_KEYSPACE: str = "meridian_audit"

    # Application
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"


settings = Settings()


def _export_langfuse_env() -> None:
    """Publish Langfuse credentials from settings into ``os.environ``.

    The Langfuse SDK v3 ``CallbackHandler`` reads ``LANGFUSE_PUBLIC_KEY`` /
    ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST`` directly from ``os.environ`` and
    self-disables if they are absent. Pydantic-settings loads ``.env`` into the
    ``settings`` object but NOT into ``os.environ``, so without this bridge the
    SDK never sees the keys and tracing is silently off. config.py stays the
    single env chokepoint — it merely re-publishes what it already loaded.
    Only non-empty values are set, so an externally-provided env var is never
    clobbered with a blank default.
    """
    for name, value in (
        ("LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY),
        ("LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY),
        ("LANGFUSE_HOST", settings.LANGFUSE_HOST),
    ):
        if value:
            os.environ.setdefault(name, value)


_export_langfuse_env()
