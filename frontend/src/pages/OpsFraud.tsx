import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getFraudQueue } from "../api";
import type { ClaimCategory, FraudClaim } from "../types";
import { CATEGORY_LABELS } from "../labels";
import { formatRupees } from "../utils/format";

function Signal({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded-full bg-molten/10 px-2.5 py-0.5 text-[12px] font-medium text-moltenText dark:text-molten">
      {children}
    </span>
  );
}

function FraudRow({ claim }: { claim: FraudClaim }) {
  const signals = [
    ...claim.extraction_signals,
    ...claim.reasons.map((r) => r.detail).filter(Boolean),
  ];
  return (
    <div className="rounded-card border border-molten/25 bg-white dark:bg-plum-800 p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link
            to={`/claims/${claim.claim_id}`}
            className="font-mono text-[14px] text-plum-800 dark:text-creamtext hover:text-coral"
          >
            {claim.claim_id}
          </Link>
          <p className="mt-0.5 text-[13px] text-plum-800/55 dark:text-creamtext/55">
            {claim.member_id} ·{" "}
            {CATEGORY_LABELS[claim.category as ClaimCategory] ?? claim.category}
            {typeof claim.confidence === "number" &&
              ` · ${Math.round(claim.confidence * 100)}% confidence`}
          </p>
        </div>
        <span className="font-mono text-sm text-plum-800/70 dark:text-creamtext/70">
          {typeof claim.approved_amount === "number"
            ? formatRupees(claim.approved_amount)
            : "—"}
        </span>
      </div>

      {claim.fraud_rule?.summary && (
        <p className="mt-3 text-sm text-plum-800/75 dark:text-creamtext/75">
          <span className="font-semibold text-moltenText dark:text-molten">Fraud rule:</span>{" "}
          {claim.fraud_rule.summary}
        </p>
      )}

      {signals.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {signals.map((s, i) => (
            <Signal key={i}>{s}</Signal>
          ))}
        </div>
      )}

      {claim.recommendations.length > 0 && (
        <ul className="mt-3 list-disc space-y-0.5 pl-5 text-[13px] text-plum-800/65 dark:text-creamtext/65">
          {claim.recommendations.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}

      <Link
        to={`/claims/${claim.claim_id}`}
        className="mt-4 inline-block text-[13px] font-semibold text-coral hover:underline"
      >
        Review claim →
      </Link>
    </div>
  );
}

export default function OpsFraud() {
  const [claims, setClaims] = useState<FraudClaim[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getFraudQueue()
      .then((c) => active && setClaims(c))
      .catch((e: unknown) =>
        active &&
        setError(e instanceof Error ? e.message : "Failed to load fraud queue.")
      );
    return () => {
      active = false;
    };
  }, []);

  return (
    <div className="mx-auto max-w-content px-6 py-10">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-4xl text-plum-800 dark:text-creamtext">Fraud queue</h1>
          <p className="mt-1 text-[14px] text-plum-800/55 dark:text-creamtext/55">
            Claims routed to manual review, with their fraud signals.
          </p>
        </div>
        <Link
          to="/ops"
          className="text-sm font-medium text-plum-800/60 dark:text-creamtext/60 hover:text-coral"
        >
          ← Dashboard
        </Link>
      </header>

      {error && (
        <div className="mb-6 rounded-xl border border-crimson/30 bg-crimson/5 dark:bg-crimson/20 px-4 py-3 text-sm text-crimson">
          {error}
        </div>
      )}

      {claims === null && !error && (
        <p className="text-sm text-plum-800/50 dark:text-creamtext/50">Loading fraud queue…</p>
      )}

      {claims && claims.length === 0 && (
        <div className="rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 px-8 py-14 text-center shadow-sm">
          <p className="font-serif text-xl text-plum-800 dark:text-creamtext">Nothing flagged</p>
          <p className="mt-1 text-sm text-plum-800/55 dark:text-creamtext/55">
            No claims are currently in manual review.
          </p>
        </div>
      )}

      {claims && claims.length > 0 && (
        <div className="grid gap-3 lg:grid-cols-2">
          {claims.map((c) => (
            <FraudRow key={c.claim_id} claim={c} />
          ))}
        </div>
      )}
    </div>
  );
}
