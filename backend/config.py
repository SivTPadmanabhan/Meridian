"""Application configuration.

This module is the ONLY place in the codebase that reads environment variables.
Everything else imports the ``settings`` singleton. No ``os.environ`` elsewhere.
"""

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

    # Embeddings
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIM: int = 384

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://meridian:password@localhost:5432/meridian"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_CACHE_TTL: int = 600

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

    # Application
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"


settings = Settings()
