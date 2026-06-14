import type { Decision, DecisionStatus } from "../types";
import { formatRupees } from "../utils/format";

// Status → Plum theme color + human label.
const STATUS_META: Record<
  DecisionStatus,
  { color: string; bg: string; ring: string; label: string }
> = {
  APPROVED: {
    color: "text-growth",
    bg: "bg-growth/[0.07]",
    ring: "border-growth/25",
    label: "Approved",
  },
  PARTIAL: {
    color: "text-sun",
    bg: "bg-sun/[0.08]",
    ring: "border-sun/30",
    label: "Partially approved",
  },
  REJECTED: {
    color: "text-crimson",
    bg: "bg-crimson/[0.06]",
    ring: "border-crimson/25",
    label: "Rejected",
  },
  MANUAL_REVIEW: {
    color: "text-sky",
    bg: "bg-sky/[0.06]",
    ring: "border-sky/25",
    label: "Manual review",
  },
};

export default function VerdictCard({ decision }: { decision: Decision }) {
  const meta = STATUS_META[decision.status] ?? STATUS_META.MANUAL_REVIEW;

  return (
    <section
      className={`overflow-hidden rounded-card border ${meta.ring} ${meta.bg} shadow-sm`}
    >
      <div className="px-6 py-8 sm:px-8">
        <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-plum-800/40 dark:text-creamtext/40">
              Decision
            </p>
            <h1
              className={`mt-1.5 font-serif text-5xl leading-none sm:text-6xl ${meta.color}`}
            >
              {meta.label}
            </h1>
          </div>

          {decision.approved_amount > 0 && (
            <div className="sm:text-right">
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-plum-800/40 dark:text-creamtext/40">
                Approved amount
              </p>
              <p className="mt-1.5 font-serif text-4xl text-plum-800 sm:text-5xl dark:text-creamtext">
                {formatRupees(decision.approved_amount)}
              </p>
            </div>
          )}
        </div>

        {decision.member_message && (
          <p className="mt-6 max-w-2xl text-[15px] leading-relaxed text-plum-800/80 dark:text-creamtext/80">
            {decision.member_message}
          </p>
        )}

        {decision.reason_codes.length > 0 && (
          <div className="mt-7">
            <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.16em] text-plum-800/40 dark:text-creamtext/40">
              Reason codes
            </p>
            <ul className="flex flex-col gap-2.5">
              {decision.reason_codes.map((rc, i) => (
                <li
                  key={`${rc.code}-${i}`}
                  className="flex flex-col gap-1.5 sm:flex-row sm:items-baseline sm:gap-3"
                >
                  <span className="inline-block w-fit rounded-md bg-plum-800/[0.06] px-2 py-1 font-mono text-[11px] font-semibold tracking-wide text-plum-800 dark:bg-creamtext/10 dark:text-creamtext">
                    {rc.code}
                  </span>
                  <span className="text-sm leading-relaxed text-plum-800/70 dark:text-creamtext/70">
                    {rc.detail}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {decision.recommendations.length > 0 && (
        <div className="border-t border-plum-800/[0.08] bg-white/50 px-6 py-5 sm:px-8 dark:border-creamtext/10 dark:bg-plum-800/50">
          <p className="mb-2.5 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-sky">
            <svg
              className="h-4 w-4"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.8}
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09Z"
              />
            </svg>
            Recommendations
          </p>
          <ul className="flex flex-col gap-1.5">
            {decision.recommendations.map((rec, i) => (
              <li
                key={i}
                className="flex gap-2 text-sm leading-relaxed text-plum-800/75 dark:text-creamtext/75"
              >
                <span className="mt-2 h-1 w-1 flex-shrink-0 rounded-full bg-sky" />
                {rec}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
