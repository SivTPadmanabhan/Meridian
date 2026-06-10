"""The provider-agnostic event shape every webhook parser produces."""

from datetime import datetime

from pydantic import BaseModel


class NormalizedEvent(BaseModel):
    """A single ingested signal, normalized across GitHub / GitLab."""

    source: str          # "github" | "gitlab"
    event_type: str      # e.g. "push", "pull_request", "check_run", "pipeline", "job"
    repo: str            # full repo / project path
    title: str           # short human-readable summary
    body_text: str       # longer text used for embedding / context
    occurred_at: datetime
    raw: dict            # the original parsed payload

    def to_document_text(self) -> str:
        """Text fed to the embedder for this event."""
        return f"{self.title}\n\n{self.body_text}".strip()
