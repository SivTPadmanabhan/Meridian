// Incident Feed (/) — Server Component, fetches server-side via lib/api.
import { IncidentFeed } from "@/components/IncidentFeed";
import { getIncidents } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page() {
  const incidents = await getIncidents();
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Incidents</h1>
        <p className="text-sm text-muted-foreground">
          Latest events triaged by the agent pipeline.
        </p>
      </div>
      <IncidentFeed incidents={incidents} />
    </div>
  );
}
