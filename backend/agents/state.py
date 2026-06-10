"""Graph state. NO human_decision field — approval is a DB concern (AD-1)."""

import operator
from typing import Annotated

from typing_extensions import TypedDict


class MeridianState(TypedDict):
    event_id: str
    incident_id: str
    event_payload: dict
    severity: str                # "P0" | "P1" | "P2" | "P3"
    confidence: float            # 0.0–1.0
    retrieved_context: Annotated[list[str], operator.add]
    root_cause: str
    suggested_action: str
    eval_scores: dict            # online eval results; {} until eval node runs
    error: str | None
