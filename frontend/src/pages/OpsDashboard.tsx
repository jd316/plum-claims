import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getOpsAnalytics, getImprovementProposals, getWorklist } from "../api";
import type { ImprovementProposal } from "../api";
import type { ClaimCategory, OpsAnalytics, WorklistItem } from "../types";
import { CATEGORY_LABELS } from "../labels";
import { formatRupees } from "../utils/format";
import { STATUS_LABEL } from "../components/statusMeta";
import StatusPill from "../components/StatusPill";

// Status → bar fill color (matches the Plum status palette used by the pills).
const STATUS_BAR: Record<string, string> = {
  APPROVED: "bg-growth",
  PARTIAL: "bg-sun",
  REJECTED: "bg-crimson",
  MANUAL_REVIEW: "bg-sky",
  UNKNOWN: "bg-plum-800/30",
};

const STATUS_ORDER = ["APPROVED", "PARTIAL", "REJECTED", "MANUAL_REVIEW", "UNKNOWN"];

function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

function StatCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-card border border-plum-800/[0.1] bg-white px-5 py-4 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-plum-800/40 dark:text-creamtext/40">
        {label}
      </p>
      <p className={`mt-1.5 font-serif text-3xl ${accent ?? "text-plum-800 dark:text-creamtext"}`}>
        {value}
      </p>
      {sub && <p className="mt-0.5 text-[12px] text-plum-800/50 dark:text-creamtext/50">{sub}</p>}
    </div>
  );
}

export default function OpsDashboard() {
  const [data, setData] = useState<OpsAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  // System self-assessment (advisory only) — best-effort; never blocks the dashboard.
  const [proposals, setProposals] = useState<ImprovementProposal[] | null>(null);
  // Actionable queue preview — claims needing review (MANUAL_REVIEW or blocked).
  const [worklist, setWorklist] = useState<WorklistItem[] | null>(null);

  useEffect(() => {
    let active = true;
    getOpsAnalytics()
      .then((d) => active && setData(d))
      .catch((e: unknown) =>
        active &&
        setError(e instanceof Error ? e.message : "Failed to load analytics.")
      );
    getImprovementProposals()
      .then((d) => active && setProposals(d.proposals))
      .catch(() => active && setProposals([]));
    getWorklist()
      .then((d) => active && setWorklist(d))
      .catch(() => active && setWorklist([]));
    return () => {
      active = false;
    };
  }, []);

  const total = data?.total_claims ?? 0;

  return (
    <div className="mx-auto max-w-content px-6 py-10">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-4xl text-plum-800 dark:text-creamtext">Ops dashboard</h1>
          <p className="mt-1 text-[14px] text-plum-800/55 dark:text-creamtext/55">
            Live analytics across every processed claim.
          </p>
        </div>
        <nav className="flex gap-2 text-sm" aria-label="Ops sections">
          <Link
            to="/ops/worklist"
            className="rounded-full bg-plum-800 px-4 py-2 font-semibold text-creamtext transition-colors hover:bg-coral"
          >
            Worklist
          </Link>
          <Link
            to="/ops/fraud"
            className="rounded-full border border-molten/40 px-4 py-2 font-semibold text-moltenText transition-colors hover:bg-molten/10"
          >
            Fraud queue{data ? ` (${data.flagged_fraud_count})` : ""}
          </Link>
          <Link
            to="/ops/policy"
            className="rounded-full border border-coral/50 px-4 py-2 font-semibold text-coral transition-colors hover:bg-coral/10"
          >
            Policy studio
          </Link>
        </nav>
      </header>

      {error && (
        <div role="alert" className="mb-6 rounded-xl border border-crimson/30 bg-crimson/5 px-4 py-3 text-sm text-crimson">
          {error}
        </div>
      )}

      {!data && !error && (
        <p className="text-sm text-plum-800/50 dark:text-creamtext/50">Loading analytics…</p>
      )}

      {data && (
        <>
          {/* Human-in-the-loop: claims the AI routed to manual review need an operator
              decision. Surface the count + a one-click link into the filtered worklist. */}
          {(data.by_status?.MANUAL_REVIEW ?? 0) > 0 && (
            <Link
              to="/ops/worklist?status=MANUAL_REVIEW"
              className="mb-6 flex items-center justify-between gap-3 rounded-card border border-molten/40 bg-molten/[0.06] px-5 py-4 transition-colors hover:bg-molten/[0.12] dark:bg-molten/10"
            >
              <span className="text-sm font-medium text-moltenText dark:text-molten">
                {data.by_status.MANUAL_REVIEW} claim
                {data.by_status.MANUAL_REVIEW === 1 ? " needs" : "s need"} your decision
              </span>
              <span className="text-sm font-semibold text-moltenText dark:text-molten">
                Review queue →
              </span>
            </Link>
          )}
          {/* Stat cards — high-density ops summary row. */}
          <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            <StatCard label="Total claims" value={String(total)} />
            <StatCard
              label="Approval rate"
              value={pct(data.approval_rate)}
              sub={`${data.decided_count} decided`}
              accent="text-growthText"
            />
            <StatCard
              label="Manual review"
              value={pct(data.manual_review_rate)}
              sub={`${data.flagged_fraud_count} flagged`}
              accent="text-skyText"
            />
            <StatCard
              label="Avg approved"
              value={formatRupees(data.avg_approved_amount)}
            />
            <StatCard label="Avg confidence" value={pct(data.avg_confidence)} />
            <StatCard
              label="Est. total cost"
              value={`₹${(data.estimated_total_cost_inr ?? 0).toFixed(2)}`}
              sub={data.avg_latency_ms ? `${data.avg_latency_ms} ms avg` : undefined}
            />
          </section>

          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            {/* Status distribution — pure-CSS stacked bar + legend. */}
            <section className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
              <h2 className="font-serif text-xl text-plum-800 dark:text-creamtext">
                Status distribution
              </h2>
              {total === 0 ? (
                <p className="mt-3 text-sm text-plum-800/50 dark:text-creamtext/50">No claims yet.</p>
              ) : (
                <>
                  <div className="mt-4 flex h-3 overflow-hidden rounded-full bg-plum-800/[0.06]">
                    {STATUS_ORDER.map((k) => {
                      const c = data.by_status[k] ?? 0;
                      if (!c) return null;
                      return (
                        <div
                          key={k}
                          className={STATUS_BAR[k] ?? STATUS_BAR.UNKNOWN}
                          style={{ width: `${(c / total) * 100}%` }}
                          title={`${STATUS_LABEL[k] ?? k}: ${c}`}
                        />
                      );
                    })}
                  </div>
                  <ul className="mt-4 space-y-1.5">
                    {STATUS_ORDER.filter((k) => data.by_status[k]).map((k) => (
                      <li
                        key={k}
                        className="flex items-center justify-between text-sm"
                      >
                        <span className="flex items-center gap-2 text-plum-800/70 dark:text-creamtext/70">
                          <span
                            className={`h-2.5 w-2.5 rounded-sm ${
                              STATUS_BAR[k] ?? STATUS_BAR.UNKNOWN
                            }`}
                          />
                          {STATUS_LABEL[k] ?? k}
                        </span>
                        <span className="font-mono text-plum-800/80 dark:text-creamtext/80">
                          {data.by_status[k]}{" "}
                          <span className="text-plum-800/40 dark:text-creamtext/40">
                            ({pct((data.by_status[k] ?? 0) / total)})
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </section>

            {/* By-category breakdown table. */}
            <section className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
              <h2 className="font-serif text-xl text-plum-800 dark:text-creamtext">By category</h2>
              {data.by_category.length === 0 ? (
                <p className="mt-3 text-sm text-plum-800/50 dark:text-creamtext/50">No claims yet.</p>
              ) : (
                <table className="mt-3 w-full text-sm">
                  <thead>
                    <tr className="border-b border-plum-800/[0.08] dark:border-creamtext/10 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-plum-800/40 dark:text-creamtext/40">
                      <th className="py-2 font-semibold">Category</th>
                      <th className="py-2 text-right font-semibold">Claims</th>
                      <th className="py-2 text-right font-semibold">
                        Total approved
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-plum-800/[0.06]">
                    {data.by_category.map((c) => (
                      <tr key={c.category}>
                        <td className="py-2 text-plum-800/75 dark:text-creamtext/75">
                          {CATEGORY_LABELS[c.category as ClaimCategory] ??
                            c.category}
                        </td>
                        <td className="py-2 text-right font-mono text-plum-800/80 dark:text-creamtext/80">
                          {c.count}
                        </td>
                        <td className="py-2 text-right font-mono text-plum-800/80 dark:text-creamtext/80">
                          {formatRupees(c.total_approved)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </div>

          {/* Needs review — an actionable queue preview so the operator lands on what
              to act on, not just analytics. Reuses /api/ops/worklist; the empty state
              ("nothing needs review") is itself a useful, reassuring signal. */}
          <section className="mt-6 rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <h2 className="font-serif text-xl text-plum-800 dark:text-creamtext">
                  Needs review
                </h2>
                {worklist && worklist.filter((r) => r.needs_review).length > 0 && (
                  <span className="rounded-full bg-molten/15 px-2 py-0.5 text-[11px] font-semibold text-molten">
                    {worklist.filter((r) => r.needs_review).length}
                  </span>
                )}
              </span>
              <Link
                to="/ops/worklist"
                className="text-[13px] font-medium text-coral hover:underline"
              >
                View all →
              </Link>
            </div>

            {worklist === null ? (
              <p className="mt-4 text-sm text-plum-800/45 dark:text-creamtext/45">
                Loading queue…
              </p>
            ) : worklist.filter((r) => r.needs_review).length === 0 ? (
              <p className="mt-4 text-sm text-plum-800/55 dark:text-creamtext/55">
                Nothing needs review right now. 🎉
              </p>
            ) : (
              <ul className="mt-3 divide-y divide-plum-800/[0.06] dark:divide-creamtext/10">
                {worklist
                  .filter((r) => r.needs_review)
                  .slice(0, 5)
                  .map((r) => (
                    <li key={r.claim_id}>
                      <Link
                        to={`/claims/${r.claim_id}`}
                        className="flex flex-wrap items-center gap-3 rounded-lg px-1 py-2.5 transition-colors hover:bg-plum-800/[0.03] dark:hover:bg-creamtext/[0.04]"
                      >
                        <StatusPill status={r.status} blocked={r.blocked} />
                        <span className="font-mono text-[12px] text-plum-800/70 dark:text-creamtext/70">
                          {r.claim_id.length > 18
                            ? `${r.claim_id.slice(0, 18)}…`
                            : r.claim_id}
                        </span>
                        <span className="text-sm text-plum-800/65 dark:text-creamtext/65">
                          {r.member_id}
                        </span>
                        <span className="text-[12px] text-plum-800/45 dark:text-creamtext/45">
                          {CATEGORY_LABELS[r.category as ClaimCategory] ?? r.category}
                        </span>
                        <span className="ml-auto font-mono text-sm text-plum-800/70 dark:text-creamtext/70">
                          {typeof r.approved_amount === "number"
                            ? formatRupees(r.approved_amount)
                            : "—"}
                        </span>
                      </Link>
                    </li>
                  ))}
              </ul>
            )}
          </section>

          {/* System self-assessment — advisory only; the system reads its own eval
              outputs and proposes improvements. Nothing here changes a decision. */}
          {proposals && proposals.length > 0 && (
            <section className="mt-6 rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
              {/* Collapsed by default: this is secondary, advisory info — it shouldn't
                  dominate the dashboard. One-line header expands to compact rows, each
                  of which expands to its full observation + proposed change. */}
              <details className="group">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-3 [&::-webkit-details-marker]:hidden">
                  <span className="flex items-center gap-2">
                    <h2 className="font-serif text-xl text-plum-800 dark:text-creamtext">
                      System self-assessment
                    </h2>
                    <span className="rounded-full bg-plum-800/[0.06] px-2 py-0.5 text-[11px] font-semibold text-plum-800/60 dark:bg-creamtext/10 dark:text-creamtext/60">
                      {proposals.length}
                    </span>
                  </span>
                  <span className="flex items-center gap-1 text-[12px] text-plum-800/45 dark:text-creamtext/45">
                    Advisory · review
                    <svg
                      className="h-4 w-4 transition-transform group-open:rotate-180"
                      fill="none"
                      viewBox="0 0 24 24"
                      strokeWidth={2}
                      stroke="currentColor"
                      aria-hidden="true"
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
                    </svg>
                  </span>
                </summary>
                <p className="mt-1 text-[12px] text-plum-800/50 dark:text-creamtext/50">
                  Proposals read from the system's own eval metrics. No proposal is auto-applied.
                </p>
                <ul className="mt-4 space-y-2">
                  {proposals.map((p, i) => (
                    <li
                      key={i}
                      className="rounded-xl border border-plum-800/[0.08] dark:border-creamtext/10"
                    >
                      <details>
                        <summary className="flex cursor-pointer list-none flex-wrap items-center gap-2 p-3 [&::-webkit-details-marker]:hidden">
                          <span className="font-mono text-[12px] font-semibold text-plum-800/80 dark:text-creamtext/80">
                            {p.area}
                          </span>
                          <span className="rounded-full bg-plum-800/[0.06] px-2 py-0.5 text-[11px] text-plum-800/60 dark:bg-creamtext/10 dark:text-creamtext/60">
                            risk: {p.risk}
                          </span>
                          <span className="rounded-full bg-plum-800/[0.06] px-2 py-0.5 text-[11px] text-plum-800/60 dark:bg-creamtext/10 dark:text-creamtext/60">
                            {p.auto_applicable ? "auto-applicable" : "human review"}
                          </span>
                        </summary>
                        <div className="px-3 pb-3">
                          <p className="text-sm text-plum-800/75 dark:text-creamtext/75">
                            {p.observation}
                          </p>
                          <p className="mt-1 text-sm text-plum-800/60 dark:text-creamtext/60">
                            <span className="font-semibold">Proposed:</span>{" "}
                            {p.proposed_change}
                          </p>
                        </div>
                      </details>
                    </li>
                  ))}
                </ul>
              </details>
            </section>
          )}
        </>
      )}
    </div>
  );
}
