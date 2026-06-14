import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { getWorklist } from "../api";
import type { ClaimCategory, WorklistFilters, WorklistItem } from "../types";
import { CATEGORY_LABELS } from "../labels";
import { formatRupees } from "../utils/format";
import StatusPill from "../components/StatusPill";
import { STATUS_LABEL } from "../components/statusMeta";

const STATUS_OPTIONS = ["APPROVED", "PARTIAL", "REJECTED", "MANUAL_REVIEW"];
const CATEGORY_OPTIONS = Object.keys(CATEGORY_LABELS) as ClaimCategory[];
const SORT_OPTIONS: { value: NonNullable<WorklistFilters["sort"]>; label: string }[] = [
  { value: "created_at", label: "Newest" },
  { value: "amount", label: "Amount" },
  { value: "confidence", label: "Confidence" },
];

// Human-friendly "age" since creation (SLA-style). Falls back to a date string.
function ageLabel(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const mins = Math.max(0, Math.floor((Date.now() - then) / 60000));
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h`;
  const days = Math.floor(hrs / 24);
  return `${days}d`;
}

const selectClass =
  "rounded-lg border border-plum-800/15 dark:border-creamtext/15 bg-white dark:bg-plum-700 px-3 py-2 text-sm text-plum-800 dark:text-creamtext focus:border-coral focus:outline-none";

export default function OpsWorklist() {
  const navigate = useNavigate();
  // Seed the status filter from ?status=… so the dashboard's "needs your decision"
  // link lands here pre-filtered to MANUAL_REVIEW.
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = useState<WorklistFilters>(() => ({
    sort: "created_at",
    status: searchParams.get("status") || undefined,
  }));
  // Rows are stored together with the filter-key they were loaded for, so the
  // visible `rows` can be derived (null while the current filters haven't loaded
  // yet → shows the loading state) without a synchronous reset in the effect.
  const [loaded, setLoaded] = useState<{ key: string; rows: WorklistItem[] } | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);

  // Debounce the free-text search so we don't refetch on every keystroke.
  const [q, setQ] = useState("");
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => ({ ...f, q: q || undefined })), 300);
    return () => clearTimeout(t);
  }, [q]);

  const key = useMemo(() => JSON.stringify(filters), [filters]);
  // null until the rows for the CURRENT filter-key have arrived (loading state).
  const rows = loaded?.key === key ? loaded.rows : null;
  useEffect(() => {
    let active = true;
    getWorklist(filters)
      .then((r) => active && setLoaded({ key, rows: r }))
      .catch((e: unknown) =>
        active &&
        setError(e instanceof Error ? e.message : "Failed to load worklist.")
      );
    return () => {
      active = false;
    };
    // `filters` (stable useState identity) and `key` (its memoized hash) change in
    // lockstep, so this re-runs exactly once per real filter change.
  }, [filters, key]);

  const reviewCount = rows?.filter((r) => r.needs_review).length ?? 0;

  return (
    <div className="mx-auto max-w-content px-6 py-10">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-4xl text-plum-800 dark:text-creamtext">Worklist</h1>
          <p className="mt-1 text-[14px] text-plum-800/55 dark:text-creamtext/55">
            The operations queue. {reviewCount > 0 && (
              <span className="font-semibold text-moltenText dark:text-molten">
                {reviewCount} need review.
              </span>
            )}
          </p>
        </div>
        <Link
          to="/ops"
          className="text-sm font-medium text-plum-800/60 dark:text-creamtext/60 hover:text-coral"
        >
          ← Dashboard
        </Link>
      </header>

      {/* Filter controls. */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search member / claim id…"
          aria-label="Search by member or claim id"
          className={`${selectClass} w-56`}
        />
        <select
          value={filters.status ?? ""}
          onChange={(e) =>
            setFilters((f) => ({ ...f, status: e.target.value || undefined }))
          }
          aria-label="Filter by status"
          className={selectClass}
        >
          <option value="">All statuses</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABEL[s] ?? s}
            </option>
          ))}
        </select>
        <select
          value={filters.category ?? ""}
          onChange={(e) =>
            setFilters((f) => ({ ...f, category: e.target.value || undefined }))
          }
          aria-label="Filter by category"
          className={selectClass}
        >
          <option value="">All categories</option>
          {CATEGORY_OPTIONS.map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABELS[c]}
            </option>
          ))}
        </select>
        <select
          value={filters.sort ?? "created_at"}
          onChange={(e) =>
            setFilters((f) => ({
              ...f,
              sort: e.target.value as WorklistFilters["sort"],
            }))
          }
          aria-label="Sort order"
          className={selectClass}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              Sort: {o.label}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="mb-6 rounded-xl border border-crimson/30 bg-crimson/5 dark:bg-crimson/20 px-4 py-3 text-sm text-crimson">
          {error}
        </div>
      )}

      {rows === null && !error && (
        <p className="text-sm text-plum-800/50 dark:text-creamtext/50">Loading queue…</p>
      )}

      {rows && rows.length === 0 && (
        <div className="rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 px-8 py-14 text-center shadow-sm">
          <p className="font-serif text-xl text-plum-800 dark:text-creamtext">No matching claims</p>
          <p className="mt-1 text-sm text-plum-800/55 dark:text-creamtext/55">
            Adjust the filters to widen the queue.
          </p>
        </div>
      )}

      {rows && rows.length > 0 && (
        <div className="overflow-hidden rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-plum-800/[0.08] dark:border-creamtext/10 text-left text-[11px] font-semibold uppercase tracking-[0.12em] text-plum-800/40 dark:text-creamtext/40">
                  <th className="px-5 py-3 font-semibold">Claim</th>
                  <th className="px-3 py-3 font-semibold">Member</th>
                  <th className="px-3 py-3 font-semibold">Category</th>
                  <th className="px-3 py-3 font-semibold">Status</th>
                  <th className="px-3 py-3 text-right font-semibold">Amount</th>
                  <th className="px-3 py-3 text-right font-semibold">Conf.</th>
                  <th className="px-3 py-3 text-right font-semibold">Age</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-plum-800/[0.06] dark:divide-creamtext/10">
                {rows.map((r) => (
                  <tr
                    key={r.claim_id}
                    onClick={() => navigate(`/claims/${r.claim_id}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        navigate(`/claims/${r.claim_id}`);
                      }
                    }}
                    tabIndex={0}
                    role="link"
                    aria-label={`Open claim ${r.claim_id}`}
                    className={`group cursor-pointer transition-colors hover:bg-cream/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-coral dark:hover:bg-creamtext/5 ${
                      r.needs_review ? "border-l-2 border-l-molten bg-molten/[0.03]" : ""
                    }`}
                  >
                    <td className="px-5 py-3">
                      <span className="font-mono text-[13px] text-plum-800 dark:text-creamtext group-hover:text-coral">
                        {r.claim_id.length > 14
                          ? `${r.claim_id.slice(0, 14)}…`
                          : r.claim_id}
                      </span>
                    </td>
                    <td className="px-3 py-3 text-plum-800/70 dark:text-creamtext/70">{r.member_id}</td>
                    <td className="px-3 py-3 text-plum-800/70 dark:text-creamtext/70">
                      {CATEGORY_LABELS[r.category as ClaimCategory] ?? r.category}
                    </td>
                    <td className="px-3 py-3">
                      <StatusPill status={r.status} blocked={r.blocked} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-right font-mono text-plum-800/80 dark:text-creamtext/80">
                      {typeof r.approved_amount === "number"
                        ? formatRupees(r.approved_amount)
                        : "—"}
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-plum-800/80 dark:text-creamtext/80">
                      {typeof r.confidence === "number"
                        ? `${Math.round(r.confidence * 100)}%`
                        : "—"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-right font-mono text-[13px] text-plum-800/50 dark:text-creamtext/50">
                      {ageLabel(r.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
