"""Parser tests — every NormalizedEvent field is populated from each fixture."""

import json
from pathlib import Path

import pytest

from backend.integrations.github import parse_github_event
from backend.integrations.gitlab import parse_gitlab_event

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _assert_all_fields(event) -> None:
    assert event.source in ("github", "gitlab")
    assert event.event_type
    assert event.repo and event.repo != "unknown/unknown"
    assert event.title
    assert event.body_text
    assert event.occurred_at is not None
    assert isinstance(event.raw, dict) and event.raw


@pytest.mark.parametrize(
    "fixture,event_type",
    [("github_push.json", "push"), ("github_ci_failure.json", "check_run")],
)
def test_github_parser_populates_all_fields(fixture: str, event_type: str) -> None:
    event = parse_github_event(event_type, _load(fixture))
    _assert_all_fields(event)
    assert event.source == "github"
    assert event.event_type == event_type


def test_gitlab_parser_populates_all_fields() -> None:
    event = parse_gitlab_event("Pipeline Hook", _load("gitlab_pipeline.json"))
    _assert_all_fields(event)
    assert event.source == "gitlab"
    assert event.event_type == "pipeline"
