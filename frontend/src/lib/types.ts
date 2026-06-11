// Shared TS interfaces — mirror the FastAPI Pydantic response models.
// Keep field names/types in sync with backend/routes/{incidents,eval}.py + main.py.

/** GET /incidents — one row of the feed (IncidentSummary). */
export interface IncidentSummary {
  id: string;
  title: string;
  severity: string | null; // "P0".."P3"
  status: string; // open | triaged_low | approved | dismissed | resolved
  confidence: number | null;
  human_decision: string | null; // pending | approved | dismissed | null
  created_at: string; // ISO-8601
}

/** One eval score row attached to an incident's AgentRun (EvalScore). */
export interface EvalScore {
  eval_type: string; // online | offline
  faithfulness: number | null;
  response_relevancy: number | null;
  hallucination_rate: number | null;
  context_precision: number | null;
  factual_correctness: number | null;
  judge_model: string;
  scored_at: string;
}

/** GET /incidents/{id} — full trace (IncidentDetail). */
export interface IncidentDetail {
  id: string;
  title: string;
  severity: string | null;
  status: string;
  created_at: string;
  resolved_at: string | null;
  triage_output: TriageOutput | null;
  analysis_output: AnalysisOutput | null;
  action_proposed: ActionProposed | null;
  human_decision: string | null; // pending | approved | dismissed | null
  completed_at: string | null;
  eval_scores: EvalScore[];
}

/** JSONB shapes the agents persist. Loosely typed — defensive on the client. */
export interface TriageOutput {
  severity?: string;
  confidence?: number;
}

export interface AnalysisOutput {
  root_cause?: string;
  retrieved_context?: string[];
}

export interface ActionProposed {
  suggested_action?: string;
}

/** GET /eval/latest — per-day, per-eval_type aggregate (EvalDayBucket). */
export interface EvalDayBucket {
  day: string; // YYYY-MM-DD
  eval_type: string;
  count: number;
  faithfulness: number | null;
  response_relevancy: number | null;
  hallucination_rate: number | null;
  context_precision: number | null;
  factual_correctness: number | null;
}

/** GET /health (HealthResponse). */
export interface Health {
  status: string; // ok | degraded
  db: boolean;
  redis: boolean;
  langfuse: boolean;
}

export type Decision = "approved" | "dismissed";
