// Server Component — renders the full agent trace for one incident.
import type { IncidentDetail as IncidentDetailType } from "@/lib/types";
import { ApprovalButtons } from "./ApprovalButtons";
import { SeverityBadge, StatusChip, timeSince } from "./badges";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border p-4">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function IncidentDetail({ incident }: { incident: IncidentDetailType }) {
  const triage = incident.triage_output ?? {};
  const analysis = incident.analysis_output ?? {};
  const action = incident.action_proposed ?? {};
  const contexts = analysis.retrieved_context ?? [];
  const onlineScores = incident.eval_scores.filter((s) => s.eval_type === "online");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <SeverityBadge severity={incident.severity} />
          <StatusChip status={incident.status} />
          <span className="text-sm text-muted-foreground">
            {timeSince(incident.created_at)}
          </span>
        </div>
        <h1 className="text-xl font-semibold tracking-tight">{incident.title}</h1>
      </div>

      {/* Agent trace: triage → analysis → action → eval */}
      <Section title="Triage">
        <p className="text-sm">
          Severity <span className="font-medium">{triage.severity ?? "—"}</span>
          {triage.confidence !== undefined && (
            <>
              {" · "}confidence{" "}
              <span className="font-medium tabular-nums">
                {(triage.confidence * 100).toFixed(0)}%
              </span>
            </>
          )}
        </p>
      </Section>

      <Section title="Analysis — root cause">
        <p className="text-sm whitespace-pre-wrap">
          {analysis.root_cause ?? "No analysis (run ended at triage)."}
        </p>
      </Section>

      {contexts.length > 0 && (
        <Section title={`Retrieved context (${contexts.length})`}>
          <details>
            <summary className="cursor-pointer text-sm text-muted-foreground">
              Show retrieved chunks
            </summary>
            <ul className="mt-2 flex flex-col gap-2">
              {contexts.map((chunk, i) => (
                <li
                  key={i}
                  className="rounded border bg-muted/30 p-2 text-xs whitespace-pre-wrap"
                >
                  {chunk}
                </li>
              ))}
            </ul>
          </details>
        </Section>
      )}

      <Section title="Action — proposed remediation">
        <p className="text-sm whitespace-pre-wrap">
          {action.suggested_action ?? "No proposal."}
        </p>
      </Section>

      <Section title="Online eval scores">
        {onlineScores.length === 0 ? (
          <p className="text-sm text-muted-foreground">Not scored.</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm tabular-nums">
            {onlineScores.map((s, i) => (
              <li key={i} className="flex flex-wrap gap-x-6 gap-y-1">
                <span>faithfulness: {fmt(s.faithfulness)}</span>
                <span>relevancy: {fmt(s.response_relevancy)}</span>
                <span>hallucination: {fmt(s.hallucination_rate)}</span>
                <span className="text-muted-foreground">judge: {s.judge_model}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* Approval gate (AD-1) — only meaningful while pending */}
      <Section title="Decision">
        {incident.human_decision === "pending" ? (
          <ApprovalButtons incidentId={incident.id} />
        ) : (
          <p className="text-sm">
            {incident.human_decision
              ? `Resolved: ${incident.human_decision}.`
              : "No decision required (triaged low)."}
          </p>
        )}
      </Section>
    </div>
  );
}

function fmt(value: number | null): string {
  return value !== null ? value.toFixed(3) : "—";
}
