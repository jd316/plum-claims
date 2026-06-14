import { useState } from "react";

import { replayClaim } from "../api";
import type { ClaimResult, ReplayResult } from "../types";

// Sub-feature A: a subtle per-claim stats row — total tokens, estimated ₹ cost,
// total latency across steps. Sub-feature B: a deterministic-replay button that
// proves "same facts → same decision" by re-running the rules with no Gemini.

function fmtCost(inr: number): string {
  if (inr <= 0) return "₹0";
  if (inr < 1) return `~₹${inr.toFixed(3)}`;
  return `~₹${inr.toFixed(2)}`;
}

export default function PipelineMeta({ result }: { result: ClaimResult }) {
  const tokens =
    (result.total_input_tokens ?? 0) + (result.total_output_tokens ?? 0);
  const cost = result.estimated_cost_inr ?? 0;
  const latencyS = (result.total_latency_ms ?? 0) / 1000;
  const steps = result.trace?.length ?? 0;

  const [replay, setReplay] = useState<ReplayResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Only offer replay for a finished, non-blocked claim with a decision.
  const canReplay = !result.blocked && Boolean(result.decision);

  const runReplay = async () => {
    setBusy(true);
    setErr(null);
    try {
      setReplay(await replayClaim(result.claim_id));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Replay failed.");
    } finally {
      setBusy(false);
    }
  };

  const hasStats = tokens > 0 || cost > 0 || latencyS > 0;

  return (
    <div className="flex flex-col gap-3 rounded-card border border-plum-800/[0.1] bg-white px-6 py-4 shadow-sm dark:border-creamtext/10 dark:bg-plum-800 sm:px-8">
      {hasStats && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[13px] text-plum-800/55 dark:text-creamtext/55">
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden>🪙</span>
            <span className="font-mono text-plum-800/70 dark:text-creamtext/70">
              {tokens.toLocaleString()}
            </span>{" "}
            tokens
          </span>
          <span className="text-plum-800/20 dark:text-creamtext/20">·</span>
          <span className="inline-flex items-center gap-1.5">
            <span className="font-mono text-plum-800/70 dark:text-creamtext/70">{fmtCost(cost)}</span>
            <span className="text-plum-800/40 dark:text-creamtext/40">est.</span>
          </span>
          <span className="text-plum-800/20 dark:text-creamtext/20">·</span>
          <span className="inline-flex items-center gap-1.5">
            <span aria-hidden>⏱</span>
            <span className="font-mono text-plum-800/70 dark:text-creamtext/70">
              {latencyS.toFixed(1)}s
            </span>
          </span>
          <span className="text-plum-800/20 dark:text-creamtext/20">·</span>
          <span className="text-plum-800/45 dark:text-creamtext/45">{steps} steps</span>
        </div>
      )}

      {canReplay && (
        <div className="flex flex-wrap items-center gap-3 border-t border-plum-800/[0.07] pt-3 dark:border-creamtext/10">
          <button
            type="button"
            onClick={runReplay}
            disabled={busy}
            className="inline-flex items-center gap-2 rounded-full border border-plum-800/15 bg-plum-800/[0.03] px-4 py-2 text-[13px] font-semibold text-plum-800/75 transition-colors hover:border-coral/40 hover:text-coral disabled:opacity-50 dark:border-creamtext/15 dark:bg-creamtext/10 dark:text-creamtext/75"
          >
            {busy ? (
              <svg
                className="h-3.5 w-3.5 animate-spin"
                viewBox="0 0 24 24"
                fill="none"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-90"
                  fill="currentColor"
                  d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4Z"
                />
              </svg>
            ) : (
              <span aria-hidden>↻</span>
            )}
            Replay decision (deterministic)
          </button>

          <span className="text-[12px] text-plum-800/40 dark:text-creamtext/40">
            Re-runs the rules from the stored facts — no LLM. Proves same facts →
            same decision.
          </span>
        </div>
      )}

      {err && (
        <div className="rounded-lg border border-crimson/25 bg-crimson/[0.06] px-3 py-2 text-[13px] text-crimson">
          {err}
        </div>
      )}

      {replay && (
        <ReplayResultBadge replay={replay} />
      )}
    </div>
  );
}

function ReplayResultBadge({ replay }: { replay: ReplayResult }) {
  if (!replay.replayable) {
    return (
      <div className="rounded-lg border border-plum-800/15 bg-plum-800/[0.03] px-3 py-2 text-[13px] text-plum-800/60 dark:border-creamtext/15 dark:bg-creamtext/10 dark:text-creamtext/60">
        Not replayable — {replay.reason ?? "no stored facts for this claim."}
      </div>
    );
  }
  const ok = replay.matches;
  const amount =
    typeof replay.replayed_amount === "number"
      ? `₹${replay.replayed_amount.toLocaleString(undefined, {
          minimumFractionDigits: 0,
          maximumFractionDigits: 2,
        })}`
      : "";
  return (
    <div
      className={`rounded-lg border px-3 py-2 text-[13px] font-medium ${
        ok
          ? "border-growth/30 bg-growth/[0.08] text-growth"
          : "border-crimson/30 bg-crimson/[0.07] text-crimson"
      }`}
    >
      {ok ? (
        <span>
          ✓ Reproduced identically — {replay.replayed_status} {amount}
        </span>
      ) : (
        <span>
          ✗ Mismatch — original {replay.original_status} (
          {replay.original_amount ?? "—"}) vs replayed {replay.replayed_status} (
          {replay.replayed_amount ?? "—"})
        </span>
      )}
    </div>
  );
}
