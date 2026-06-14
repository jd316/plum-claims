import { useState } from "react";

import type { TraceEntry, TraceStatus } from "../types";

// Status → chip color. Degraded/ERROR are surfaced as molten.
const STATUS_META: Record<
  TraceStatus,
  { dot: string; chip: string; label: string }
> = {
  PASS: {
    dot: "bg-growth",
    chip: "bg-growth/12 text-growth",
    label: "Pass",
  },
  FAIL: {
    dot: "bg-crimson",
    chip: "bg-crimson/10 text-crimson",
    label: "Fail",
  },
  FLAG: {
    dot: "bg-sun",
    chip: "bg-sun/15 text-sunText dark:bg-sun/20 dark:text-sun",
    label: "Flag",
  },
  ERROR: {
    dot: "bg-molten",
    chip: "bg-molten/12 text-molten",
    label: "Error",
  },
  SKIPPED: {
    dot: "bg-plum-800/30",
    chip: "bg-plum-800/[0.07] text-plum-800/50 dark:text-creamtext/50",
    label: "Skipped",
  },
  INFO: {
    dot: "bg-plum-800/60",
    chip: "bg-plum-800/[0.07] text-plum-800/70 dark:text-creamtext/70",
    label: "Info",
  },
};

function StatusChip({ status, degraded }: { status: TraceStatus; degraded: boolean }) {
  // Degraded rows read as molten regardless of the underlying status.
  const meta = degraded ? STATUS_META.ERROR : STATUS_META[status];
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${meta.chip}`}
    >
      {degraded ? "Degraded" : meta.label}
    </span>
  );
}

function MonoTag({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded bg-plum-800/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-plum-800/70 dark:text-creamtext/70">
      {children}
    </span>
  );
}

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:gap-3">
      <span className="w-32 flex-shrink-0 text-[11px] font-semibold uppercase tracking-wider text-plum-800/40 dark:text-creamtext/40">
        {label}
      </span>
      <div className="min-w-0 flex-1 text-[13px] text-plum-800/75 dark:text-creamtext/75">{children}</div>
    </div>
  );
}

function TraceRow({ entry, isLast }: { entry: TraceEntry; isLast: boolean }) {
  const [open, setOpen] = useState(false);
  const meta = entry.degraded ? STATUS_META.ERROR : STATUS_META[entry.status];
  // Sub-feature A: total tokens for this step (input + output), null if untracked.
  const tokenCount =
    entry.input_tokens != null || entry.output_tokens != null
      ? (entry.input_tokens ?? 0) + (entry.output_tokens ?? 0)
      : null;
  const hasDetail =
    Object.keys(entry.detail ?? {}).length > 0 ||
    entry.policy_refs.length > 0 ||
    Boolean(entry.model) ||
    entry.degraded ||
    typeof entry.confidence_delta === "number";

  return (
    <li className="relative pl-10">
      {/* connector line */}
      {!isLast && (
        <span className="absolute left-[11px] top-7 bottom-0 w-px bg-plum-800/[0.12]" />
      )}
      {/* dot */}
      <span
        className={`absolute left-[5px] top-[7px] h-3 w-3 rounded-full ring-4 ring-cream dark:ring-plum-800 ${meta.dot}`}
      />

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group flex w-full items-start gap-3 rounded-lg py-1.5 pr-2 text-left transition-colors hover:bg-plum-800/[0.03]"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <StatusChip status={entry.status} degraded={entry.degraded} />
            <span className="font-semibold text-plum-800 dark:text-creamtext">{entry.step}</span>
            <span className="text-[13px] text-plum-800/45 dark:text-creamtext/45">{entry.agent}</span>
          </div>
          <p className="mt-1 text-sm leading-relaxed text-plum-800/70 dark:text-creamtext/70">
            {entry.summary}
          </p>
        </div>

        <div className="flex flex-shrink-0 items-center gap-2 pt-0.5">
          <span className="font-mono text-[11px] text-plum-800/35 dark:text-creamtext/35">
            {entry.duration_ms}ms
            {tokenCount !== null && (
              <span className="text-plum-800/30 dark:text-creamtext/30"> · {tokenCount.toLocaleString()} tok</span>
            )}
          </span>
          {hasDetail && (
            <svg
              className={`h-4 w-4 text-plum-800/40 dark:text-creamtext/40 transition-transform ${
                open ? "rotate-180" : ""
              }`}
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={2}
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="m19.5 8.25-7.5 7.5-7.5-7.5"
              />
            </svg>
          )}
        </div>
      </button>

      {open && hasDetail && (
        <div className="mb-2 mt-2 flex flex-col gap-3 rounded-xl border border-plum-800/[0.08] bg-white px-4 py-4 dark:border-creamtext/10 dark:bg-plum-700">
          {entry.degraded && (
            <div className="flex items-center gap-2 rounded-lg border border-molten/25 bg-molten/[0.06] px-3 py-2 text-[13px] font-medium text-molten">
              <span>⚠ Component degraded</span>
              {entry.failure_mode && (
                <span className="font-mono text-[12px] opacity-80">
                  {entry.failure_mode}
                </span>
              )}
            </div>
          )}

          {entry.model && (
            <DetailRow label="Model">
              <span className="font-mono">{entry.model}</span>
            </DetailRow>
          )}

          {tokenCount !== null && (
            <DetailRow label="Tokens">
              <span className="font-mono">
                {tokenCount.toLocaleString()} total
                <span className="text-plum-800/45 dark:text-creamtext/45">
                  {" "}
                  ({(entry.input_tokens ?? 0).toLocaleString()} in ·{" "}
                  {(entry.output_tokens ?? 0).toLocaleString()} out)
                </span>
              </span>
            </DetailRow>
          )}

          {typeof entry.confidence_delta === "number" && (
            <DetailRow label="Confidence Δ">
              <span
                className={`font-mono ${
                  entry.confidence_delta < 0 ? "text-crimson" : "text-growth"
                }`}
              >
                {entry.confidence_delta > 0 ? "+" : ""}
                {entry.confidence_delta}
              </span>
            </DetailRow>
          )}

          {entry.policy_refs.length > 0 && (
            <DetailRow label="Policy refs">
              <div className="flex flex-wrap gap-1.5">
                {entry.policy_refs.map((ref, i) => (
                  <MonoTag key={`${ref}-${i}`}>{ref}</MonoTag>
                ))}
              </div>
            </DetailRow>
          )}

          {Object.keys(entry.detail ?? {}).length > 0 && (
            <DetailRow label="Detail">
              <pre className="max-h-72 overflow-auto rounded-lg bg-plum-900/[0.04] p-3 font-mono text-[12px] leading-relaxed text-plum-800/80 dark:text-creamtext/80">
                {JSON.stringify(entry.detail, null, 2)}
              </pre>
            </DetailRow>
          )}

          <DetailRow label="Duration">
            <span className="font-mono">{entry.duration_ms}ms</span>
          </DetailRow>
        </div>
      )}
    </li>
  );
}

export default function TraceTimeline({ trace }: { trace: TraceEntry[] }) {
  const ordered = [...trace].sort((a, b) => a.seq - b.seq);

  return (
    <section className="overflow-hidden rounded-card border border-plum-800/[0.12] bg-cream shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
      <div className="border-b border-plum-800/[0.08] bg-white px-6 py-5 sm:px-8 dark:border-creamtext/10 dark:bg-plum-700">
        <h2 className="font-serif text-2xl text-plum-800 dark:text-creamtext">Decision trace</h2>
        <p className="mt-1 text-sm text-plum-800/55 dark:text-creamtext/55">
          Every step the pipeline ran, in order. Click any step to inspect what
          it checked, the policy refs, the model, and any degradation.
        </p>
      </div>

      <ol className="px-6 py-6 sm:px-8">
        {ordered.map((entry, i) => (
          <TraceRow
            key={entry.seq}
            entry={entry}
            isLast={i === ordered.length - 1}
          />
        ))}
      </ol>
    </section>
  );
}
