// Small presentational helpers shared by the feed and detail views.
// Pure Server Components (no client JS) — they only render styled spans.

const SEVERITY_STYLES: Record<string, string> = {
  P0: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  P1: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  P2: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950 dark:text-yellow-300",
  P3: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
};

const STATUS_STYLES: Record<string, string> = {
  open: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  triaged_low: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  approved: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
  dismissed: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
  resolved: "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
};

export function SeverityBadge({ severity }: { severity: string | null }) {
  const label = severity ?? "—";
  const style = (severity && SEVERITY_STYLES[severity]) ?? SEVERITY_STYLES.P3;
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-semibold ${style}`}
    >
      {label}
    </span>
  );
}

export function StatusChip({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.open;
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${style}`}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}

/** "3m ago" / "2h ago" / "5d ago" from an ISO timestamp. */
export function timeSince(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}
