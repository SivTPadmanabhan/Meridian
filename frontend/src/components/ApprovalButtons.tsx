"use client";

import { useState, useTransition } from "react";

import { approveIncident } from "@/app/actions";
import type { Decision } from "@/lib/types";

/**
 * Client island: the only browser-interactive part of the detail page.
 * Invokes the approveIncident Server Action; the action revalidates the
 * affected pages so the status chip updates without a manual refresh.
 */
export function ApprovalButtons({ incidentId }: { incidentId: string }) {
  const [isPending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);

  function decide(decision: Decision) {
    setMessage(null);
    startTransition(async () => {
      const result = await approveIncident(incidentId, decision);
      setMessage(result.message);
    });
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-3">
        <button
          type="button"
          disabled={isPending}
          onClick={() => decide("approved")}
          className="rounded-md bg-green-600 px-4 py-2 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
        >
          {isPending ? "Working…" : "Approve"}
        </button>
        <button
          type="button"
          disabled={isPending}
          onClick={() => decide("dismissed")}
          className="rounded-md border px-4 py-2 text-sm font-medium hover:bg-muted disabled:opacity-50"
        >
          Dismiss
        </button>
      </div>
      {message && <p className="text-sm text-muted-foreground">{message}</p>}
    </div>
  );
}
