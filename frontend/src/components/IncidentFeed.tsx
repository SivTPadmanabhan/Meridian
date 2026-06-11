// Server Component — renders the incident feed table from server-fetched rows.
import Link from "next/link";

import type { IncidentSummary } from "@/lib/types";
import { SeverityBadge, StatusChip, timeSince } from "./badges";

export function IncidentFeed({ incidents }: { incidents: IncidentSummary[] }) {
  if (incidents.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No incidents yet. Send a webhook event to populate the feed.
      </p>
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-4 py-3 font-medium">Severity</th>
            <th className="px-4 py-3 font-medium">Title</th>
            <th className="px-4 py-3 font-medium">Status</th>
            <th className="px-4 py-3 font-medium">Confidence</th>
            <th className="px-4 py-3 font-medium">Age</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {incidents.map((incident) => (
            <tr key={incident.id} className="hover:bg-muted/30">
              <td className="px-4 py-3">
                <SeverityBadge severity={incident.severity} />
              </td>
              <td className="px-4 py-3">
                <Link
                  href={`/incidents/${incident.id}`}
                  className="font-medium text-foreground hover:underline"
                >
                  {incident.title}
                </Link>
              </td>
              <td className="px-4 py-3">
                <StatusChip status={incident.status} />
              </td>
              <td className="px-4 py-3 tabular-nums text-muted-foreground">
                {incident.confidence !== null
                  ? `${(incident.confidence * 100).toFixed(0)}%`
                  : "—"}
              </td>
              <td className="px-4 py-3 text-muted-foreground">
                {timeSince(incident.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
