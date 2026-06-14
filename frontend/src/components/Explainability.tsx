// "What would change this?" — counterfactual explanations + a what-if simulator,
// both computed on the DETERMINISTIC decision layer (no Gemini) over the stored
// claim facts, so they are exact and instant. Read-only: nothing here mutates a
// stored claim. Rendered on the Claim page for non-approved / partial decisions.

import { useEffect, useState } from "react";

import { getCounterfactuals, whatIf } from "../api";
import type { Counterfactual, WhatIfResult } from "../api";
import StatusPill from "./StatusPill";

const fmtInr = (n: number | null | undefined) =>
  n == null ? "—" : `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

// --- Counterfactual list ----------------------------------------------------

function CounterfactualList({ items }: { items: Counterfactual[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-col gap-3">
      {items.map((cf, i) => (
        <li
          key={`${cf.reason}-${i}`}
          className={[
            "rounded-xl border px-4 py-3.5",
            cf.achievable
              ? "border-growth/30 bg-growth/[0.05]"
              : "border-plum-800/12 dark:border-creamtext/10 bg-plum-800/[0.03] dark:bg-creamtext/10",
          ].join(" ")}
        >
          <div className="flex items-start justify-between gap-3">
            <p className="text-[14px] leading-relaxed text-plum-800 dark:text-creamtext">{cf.change}</p>
            {cf.achievable ? (
              <span className="flex-shrink-0 rounded-full bg-growth/15 dark:bg-growth/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-growthText dark:text-growth">
                Possible
              </span>
            ) : (
              <span className="flex-shrink-0 rounded-full bg-plum-800/[0.07] dark:bg-creamtext/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-plum-800/55 dark:text-creamtext/55">
                Not possible
              </span>
            )}
          </div>
          {cf.achievable && (
            <p className="mt-2 flex items-center gap-1.5 text-[13px] text-plum-800/65 dark:text-creamtext/65">
              <span className="text-plum-800/40 dark:text-creamtext/40">→</span>
              <StatusPill status={cf.resulting_decision} />
              {cf.resulting_amount != null && (
                <span className="font-medium text-plum-800 dark:text-creamtext">
                  {fmtInr(cf.resulting_amount)}
                </span>
              )}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

// --- What-if simulator ------------------------------------------------------

function WhatIfPanel({
  claimId,
  baseAmount,
  baseDate,
  baseNetwork,
}: {
  claimId: string;
  baseAmount: number;
  baseDate: string;
  baseNetwork: boolean;
}) {
  const [amount, setAmount] = useState<number>(baseAmount);
  const [isNetwork, setIsNetwork] = useState<boolean>(baseNetwork);
  const [tdate, setTdate] = useState<string>(baseDate);
  const [result, setResult] = useState<WhatIfResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const r = await whatIf(claimId, {
        claimed_amount: amount,
        is_network: isNetwork,
        treatment_date: tdate,
      });
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Simulation failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-xl border border-plum-800/12 dark:border-creamtext/10 bg-cream/40 dark:bg-plum-900 px-4 py-4">
      <p className="mb-3 text-[13px] font-semibold uppercase tracking-wide text-plum-800/55 dark:text-creamtext/55">
        What-if simulator
      </p>
      <div className="flex flex-col gap-3.5">
        <label className="flex flex-col gap-1.5">
          <span className="text-[12px] text-plum-800/60 dark:text-creamtext/60">
            Claimed amount — {fmtInr(amount)}
          </span>
          <input
            type="range"
            min={0}
            max={Math.max(baseAmount * 1.5, 30000)}
            step={500}
            value={amount}
            onChange={(e) => setAmount(Number(e.target.value))}
            className="accent-coral"
          />
        </label>

        <div className="flex items-center justify-between">
          <span className="text-[12px] text-plum-800/60 dark:text-creamtext/60">Network hospital</span>
          <button
            type="button"
            role="switch"
            aria-label="Toggle network hospital"
            aria-checked={isNetwork}
            onClick={() => setIsNetwork((v) => !v)}
            className={[
              "relative h-6 w-11 rounded-full transition-colors",
              isNetwork ? "bg-coral" : "bg-plum-800/15",
            ].join(" ")}
          >
            <span
              className={[
                "absolute top-0.5 h-5 w-5 rounded-full bg-white dark:bg-plum-700 shadow transition-transform",
                isNetwork ? "translate-x-[22px]" : "translate-x-0.5",
              ].join(" ")}
            />
          </button>
        </div>

        <label className="flex items-center justify-between">
          <span className="text-[12px] text-plum-800/60 dark:text-creamtext/60">Treatment date</span>
          <input
            type="date"
            value={tdate}
            onChange={(e) => setTdate(e.target.value)}
            className="rounded-lg border border-plum-800/15 dark:border-creamtext/15 bg-white dark:bg-plum-700 px-2.5 py-1.5 text-[13px] text-plum-800 dark:text-creamtext outline-none focus:border-coral"
          />
        </label>

        <button
          type="button"
          onClick={run}
          disabled={loading}
          className="self-start rounded-full bg-coral px-4 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-plum-800 disabled:opacity-60"
        >
          {loading ? "Simulating…" : "Simulate"}
        </button>

        {error && <p className="text-[12px] font-medium text-crimson">{error}</p>}

        {result && (
          <div className="mt-1 flex items-center gap-3 rounded-lg border border-plum-800/10 dark:border-creamtext/10 bg-white dark:bg-plum-700 px-3.5 py-3">
            <div className="flex flex-col items-start gap-1">
              <span className="text-[10px] uppercase tracking-wide text-plum-800/40 dark:text-creamtext/40">
                Before
              </span>
              <StatusPill status={result.before.status} />
              <span className="text-[12px] text-plum-800/60 dark:text-creamtext/60">
                {fmtInr(result.before.approved_amount)}
              </span>
            </div>
            <span className="text-plum-800/30 dark:text-creamtext/30">→</span>
            <div className="flex flex-col items-start gap-1">
              <span className="text-[10px] uppercase tracking-wide text-plum-800/40 dark:text-creamtext/40">
                After
              </span>
              <StatusPill status={result.after.status} />
              <span className="text-[12px] font-medium text-plum-800 dark:text-creamtext">
                {fmtInr(result.after.approved_amount)}
              </span>
            </div>
            {result.diff.amount_delta !== 0 && (
              <span
                className={[
                  "ml-auto text-[13px] font-semibold",
                  result.diff.amount_delta > 0 ? "text-growthText dark:text-growth" : "text-crimson",
                ].join(" ")}
              >
                {result.diff.amount_delta > 0 ? "+" : ""}
                {fmtInr(result.diff.amount_delta)}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// --- Container --------------------------------------------------------------

export default function Explainability({
  claimId,
  status,
}: {
  claimId: string;
  status: string;
}) {
  const [open, setOpen] = useState(true);
  const [cfs, setCfs] = useState<Counterfactual[] | null>(null);
  const [base, setBase] = useState<{
    claimed_amount: number;
    treatment_date: string;
    is_network: boolean;
  } | null>(null);
  // Only meaningful for non-approved outcomes; an APPROVED claim has nothing to flip.
  const relevant = status !== "APPROVED";
  // Start "loaded" when there's nothing to fetch, so no synchronous reset is needed.
  const [loading, setLoading] = useState(relevant);

  useEffect(() => {
    if (!relevant) return;
    let alive = true;
    getCounterfactuals(claimId)
      .then((r) => {
        if (alive) {
          setCfs(r.counterfactuals);
          setBase(r.base);
        }
      })
      .catch(() => {
        if (alive) setCfs([]);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [claimId, relevant]);

  if (!relevant) return null;

  return (
    <div className="overflow-hidden rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-6 py-4 text-left sm:px-8"
      >
        <span className="flex items-center gap-2.5">
          <svg
            className="h-5 w-5 text-coral"
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
          <span className="font-serif text-lg text-plum-800 dark:text-creamtext">
            What would change this?
          </span>
        </span>
        <svg
          className={`h-5 w-5 text-plum-800/40 dark:text-creamtext/40 transition-transform ${
            open ? "rotate-180" : ""
          }`}
          fill="none"
          viewBox="0 0 24 24"
          strokeWidth={2}
          stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>

      {open && (
        <div className="flex flex-col gap-5 border-t border-plum-800/[0.07] dark:border-creamtext/10 px-6 py-5 sm:px-8">
          <p className="text-[13px] leading-relaxed text-plum-800/55 dark:text-creamtext/55">
            These are computed exactly from the policy rules — no AI guessing. Each
            change is re-run through the same deterministic decision engine.
          </p>

          {loading ? (
            <p className="text-sm text-plum-800/50 dark:text-creamtext/50">Computing counterfactuals…</p>
          ) : cfs && cfs.length > 0 ? (
            <CounterfactualList items={cfs} />
          ) : (
            <p className="text-sm text-plum-800/50 dark:text-creamtext/50">
              No single change to this claim would flip the decision.
            </p>
          )}

          {base && (
            <WhatIfPanel
              claimId={claimId}
              baseAmount={base.claimed_amount}
              baseDate={base.treatment_date}
              baseNetwork={base.is_network}
            />
          )}
        </div>
      )}
    </div>
  );
}
