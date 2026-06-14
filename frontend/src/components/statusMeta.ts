// Shared decision-status pill metadata — colors + labels mirror the Plum status
// palette. Used by the Claims list and the Ops worklist / fraud views so the
// pills are identical everywhere. A blocked claim takes precedence over its raw
// status.

export const STATUS_PILL: Record<string, string> = {
  APPROVED: "bg-growth/12 text-growthText dark:bg-growth/20 dark:text-growth",
  PARTIAL: "bg-sun/15 text-sunText dark:bg-sun/20 dark:text-sun",
  REJECTED: "bg-crimson/10 text-crimson",
  MANUAL_REVIEW: "bg-sky/10 text-skyText dark:bg-sky/20 dark:text-sky",
  BLOCKED: "bg-molten/10 text-moltenText dark:text-molten",
};

export const STATUS_LABEL: Record<string, string> = {
  APPROVED: "Approved",
  PARTIAL: "Partial",
  REJECTED: "Rejected",
  MANUAL_REVIEW: "Manual review",
  BLOCKED: "Blocked",
};

export function statusKey(status?: string | null, blocked?: boolean): string {
  return blocked ? "BLOCKED" : status ?? "";
}
