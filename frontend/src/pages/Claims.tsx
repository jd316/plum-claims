import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { listClaims } from "../api";
import type { ClaimCategory, ClaimSummary } from "../types";
import { CATEGORY_LABELS } from "../labels";
import { formatRupees } from "../utils/format";
import StatusPill from "../components/StatusPill";

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function Claims() {
  const [claims, setClaims] = useState<ClaimSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listClaims()
      .then((c) => {
        if (active) setClaims(c);
      })
      .catch((err: unknown) => {
        if (active)
          setError(
            err instanceof Error ? err.message : "Failed to load claims."
          );
      });
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="mx-auto max-w-content px-6 py-12 sm:py-16">
      <header className="mb-8">
        <h1 className="font-serif text-4xl text-plum-800 sm:text-5xl dark:text-creamtext">Claims</h1>
        <p className="mt-2 text-[15px] text-plum-800/60 dark:text-creamtext/60">
          Every claim processed through the pipeline. Open one to see the full
          decision trace.
        </p>
      </header>

      {error && (
        <div className="mb-6 rounded-xl border border-crimson/30 bg-crimson/5 px-4 py-3 text-sm text-crimson">
          {error}
        </div>
      )}

      {claims === null && !error && (
        <p className="text-sm text-plum-800/50 dark:text-creamtext/50">Loading claims…</p>
      )}

      {claims && claims.length === 0 && (
        <div className="rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 px-8 py-16 text-center shadow-sm">
          <p className="font-serif text-2xl text-plum-800 dark:text-creamtext">No claims yet</p>
          <p className="mt-2 text-sm text-plum-800/55 dark:text-creamtext/55">
            Submit one to see it appear here.
          </p>
          <Link
            to="/"
            className="mt-6 inline-block rounded-full bg-coral px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-plum-800"
          >
            Submit a claim
          </Link>
        </div>
      )}

      {claims && claims.length > 0 && (
        <div className="overflow-hidden rounded-card border border-plum-800/[0.12] bg-white shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Claims processed through the pipeline</caption>
              <thead>
                <tr className="border-b border-plum-800/[0.08] text-left text-[11px] font-semibold uppercase tracking-[0.12em] text-plum-800/40 dark:border-creamtext/10 dark:text-creamtext/40">
                  <th className="px-6 py-4 font-semibold">Claim</th>
                  <th className="px-4 py-4 font-semibold">Member</th>
                  <th className="px-4 py-4 font-semibold">Category</th>
                  <th className="px-4 py-4 font-semibold">Status</th>
                  <th className="px-4 py-4 text-right font-semibold">Approved</th>
                  <th className="px-4 py-4 text-right font-semibold">
                    Confidence
                  </th>
                  <th className="px-6 py-4 text-right font-semibold">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-plum-800/[0.06] dark:divide-creamtext/[0.07]">
                {claims.map((c) => (
                  <tr
                    key={c.claim_id}
                    className="group cursor-pointer transition-colors hover:bg-cream/70 dark:hover:bg-plum-700/50"
                  >
                    <td className="px-6 py-4">
                      <Link
                        to={`/claims/${c.claim_id}`}
                        className="font-mono text-[13px] text-plum-800 group-hover:text-coral dark:text-creamtext"
                      >
                        {c.claim_id.length > 12
                          ? `${c.claim_id.slice(0, 12)}…`
                          : c.claim_id}
                      </Link>
                    </td>
                    <td className="px-4 py-4 text-plum-800/70 dark:text-creamtext/70">{c.member_id}</td>
                    <td className="px-4 py-4 text-plum-800/70 dark:text-creamtext/70">
                      {CATEGORY_LABELS[c.category as ClaimCategory] ??
                        c.category}
                    </td>
                    <td className="px-4 py-4">
                      <StatusPill status={c.status} blocked={c.blocked} />
                    </td>
                    <td className="whitespace-nowrap px-4 py-4 text-right font-mono text-plum-800/80 dark:text-creamtext/80">
                      {typeof c.approved_amount === "number"
                        ? formatRupees(c.approved_amount)
                        : "—"}
                    </td>
                    <td className="px-4 py-4 text-right font-mono text-plum-800/80 dark:text-creamtext/80">
                      {typeof c.confidence === "number"
                        ? `${Math.round(c.confidence * 100)}%`
                        : "—"}
                    </td>
                    <td className="whitespace-nowrap px-6 py-4 text-right text-[13px] text-plum-800/50 dark:text-creamtext/50">
                      {formatDate(c.created_at)}
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
