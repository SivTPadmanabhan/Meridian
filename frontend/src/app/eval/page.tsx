// Eval Health (/eval) — Server Component.
// Fetches eval metrics + incidents server-side; passes shaped data into the
// EvalMetricsChart client island and renders the pending-approval queue.
import Link from "next/link";

import { ApprovalButtons } from "@/components/ApprovalButtons";
import { SeverityBadge } from "@/components/badges";
import {
  EvalMetricsChart,
  type EvalChartPoint,
} from "@/components/EvalMetricsChart";
import { getEvalMetrics, getIncidents } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const [buckets, incidents] = await Promise.all([
    getEvalMetrics(),
    getIncidents(),
  ]);

  // Online buckets only, oldest → newest for the time series.
  const points: EvalChartPoint[] = buckets
    .filter((b) => b.eval_type === "online")
    .map((b) => ({
      day: b.day,
      faithfulness: b.faithfulness,
      hallucination_rate: b.hallucination_rate,
    }))
    .reverse();

  const pending = incidents.filter((i) => i.human_decision === "pending");

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Eval Health</h1>
          <p className="text-sm text-muted-foreground">
            Online quality over the last 30 days. Targets: faithfulness ≥ 0.85,
            hallucination ≤ 0.10.
          </p>
        </div>
        <div className="rounded-lg border p-4">
          <EvalMetricsChart data={points} />
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <h2 className="text-lg font-semibold tracking-tight">
          Approval Queue ({pending.length})
        </h2>
        {pending.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No proposals awaiting a decision.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {pending.map((incident) => (
              <li
                key={incident.id}
                className="flex flex-wrap items-center justify-between gap-4 rounded-lg border p-4"
              >
                <div className="flex items-center gap-3">
                  <SeverityBadge severity={incident.severity} />
                  <Link
                    href={`/incidents/${incident.id}`}
                    className="font-medium hover:underline"
                  >
                    {incident.title}
                  </Link>
                </div>
                <ApprovalButtons incidentId={incident.id} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
