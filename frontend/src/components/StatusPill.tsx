// Shared decision-status pill — colors + labels mirror the Plum status palette.
// Used by the Claims list and the Ops worklist / fraud views so the pills are
// identical everywhere. A blocked claim takes precedence over its raw status.

import { STATUS_LABEL, STATUS_PILL, statusKey } from "./statusMeta";

export default function StatusPill({
  status,
  blocked,
}: {
  status?: string | null;
  blocked?: boolean;
}) {
  const key = statusKey(status, blocked);
  const cls = STATUS_PILL[key] ?? "bg-plum-800/[0.07] text-plum-800/55 dark:bg-creamtext/10 dark:text-creamtext/55";
  const label = STATUS_LABEL[key] ?? key ?? "—";
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${cls}`}
    >
      {label}
    </span>
  );
}
