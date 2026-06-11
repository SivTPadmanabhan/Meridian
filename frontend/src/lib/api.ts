// Server-side typed fetch wrappers — the ONLY place that calls FastAPI.
// Runs server-to-server (RSC / Server Actions), so there is no CORS and the
// browser never holds an API client. Never import this into a "use client" file.

import type {
  Decision,
  EvalDayBucket,
  Health,
  IncidentDetail,
  IncidentSummary,
} from "./types";

const BASE_URL = process.env.INTERNAL_API_URL ?? "http://localhost:8000";

/** Live ops data — always fetch fresh (no Next data cache). */
async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`GET ${path} failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export function getIncidents(): Promise<IncidentSummary[]> {
  return getJSON<IncidentSummary[]>("/incidents");
}

/** Returns null on 404 so the detail page can render notFound(). */
export async function getIncident(id: string): Promise<IncidentDetail | null> {
  const res = await fetch(`${BASE_URL}/incidents/${id}`, { cache: "no-store" });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`GET /incidents/${id} failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as IncidentDetail;
}

export function getEvalMetrics(): Promise<EvalDayBucket[]> {
  return getJSON<EvalDayBucket[]>("/eval/latest");
}

export function getHealth(): Promise<Health> {
  return getJSON<Health>("/health");
}

/** POST /incidents/{id}/approve — invoked from the approveIncident Server Action. */
export async function postApproval(
  id: string,
  decision: Decision,
): Promise<{ ok: boolean; status: number }> {
  const res = await fetch(`${BASE_URL}/incidents/${id}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision }),
    cache: "no-store",
  });
  return { ok: res.ok, status: res.status };
}
