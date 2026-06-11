"use server";

import { revalidatePath } from "next/cache";

import { postApproval } from "@/lib/api";
import type { Decision } from "@/lib/types";

export interface ApprovalResult {
  ok: boolean;
  message: string;
}

/**
 * Approve or dismiss a pending proposal (AD-1: resolution is a DB update).
 * POSTs to FastAPI, then revalidates the feed, detail, and eval pages so the
 * updated status chip / queue render without a manual refresh.
 */
export async function approveIncident(
  id: string,
  decision: Decision,
): Promise<ApprovalResult> {
  const { ok, status } = await postApproval(id, decision);

  if (!ok) {
    const message =
      status === 409
        ? "Already resolved — no longer pending."
        : status === 404
          ? "Incident not found."
          : `Approval failed (${status}).`;
    return { ok: false, message };
  }

  revalidatePath("/");
  revalidatePath(`/incidents/${id}`);
  revalidatePath("/eval");
  return { ok: true, message: `Incident ${decision}.` };
}
