// Incident Detail (/incidents/:id) — Server Component.
// Next.js 16: `params` is a Promise and must be awaited.
import { notFound } from "next/navigation";

import { IncidentDetail } from "@/components/IncidentDetail";
import { getIncident } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const incident = await getIncident(id);
  if (incident === null) {
    notFound();
  }
  return <IncidentDetail incident={incident} />;
}
