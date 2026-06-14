// Operator final-decision panel (human-in-the-loop).
//
// The AI auto-adjudicates; an operator makes the FINAL call here — resolve a
// MANUAL_REVIEW (e.g. a fraud flag) or override the AI outcome — with a required
// note. The backend sets the decision, persists it, and writes an append-only audit
// row. Gated to ops when auth is ON; open when auth is OFF. When the current status
// is MANUAL_REVIEW the panel is highlighted as awaiting a decision.

import { useState } from "react";

import { operatorDecision, markOutcome } from "../api";
import type { ClaimResult } from "../types";
import { useAuth } from "../auth-context";

type OpStatus = "APPROVED" | "PARTIAL" | "REJECTED";
const STATUSES: { value: OpStatus; label: string }[] = [
  { value: "APPROVED", label: "Approve" },
  { value: "PARTIAL", label: "Partial" },
  { value: "REJECTED", label: "Reject" },
];

export default function OpsDecisionPanel({
  claim,
  onDecided,
}: {
  claim: ClaimResult;
  onDecided?: () => void;
}) {
  const { enabled, user } = useAuth();
  const isOps = !enabled || user?.role === "ops";

  const current = claim.decision?.status ?? null;
  const awaiting = current === "MANUAL_REVIEW";
  const [status, setStatus] = useState<OpStatus>(awaiting ? "APPROVED" : "APPROVED");
  const [amount, setAmount] = useState<string>(
    claim.decision?.approved_amount != null ? String(claim.decision.approved_amount) : ""
  );
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [labeled, setLabeled] = useState<boolean | null>(null);
  const [labelBusy, setLabelBusy] = useState(false);

  // No decision to act on (blocked claim) or not an operator → render nothing.
  if (!isOps || !claim.decision) return null;

  async function label(correct: boolean) {
    setLabelBusy(true);
    setError(null);
    try {
      await markOutcome(claim.claim_id, correct);
      setLabeled(correct);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the outcome label.");
    } finally {
      setLabelBusy(false);
    }
  }

  async function apply() {
    const trimmed = note.trim();
    if (!trimmed) {
      setError("A decision note is required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const amt =
        status === "REJECTED" ? undefined : amount.trim() === "" ? undefined : Number(amount);
      await operatorDecision(claim.claim_id, {
        status,
        approved_amount: Number.isFinite(amt as number) ? (amt as number) : undefined,
        note: trimmed,
      });
      setNote("");
      onDecided?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not record the decision.");
    } finally {
      setBusy(false);
    }
  }

  const inputClass =
    "w-full rounded-xl border border-plum-800/15 bg-white px-3.5 py-2.5 text-sm text-plum-800 outline-none transition-colors placeholder:text-plum-800/30 focus:border-coral focus:ring-2 focus:ring-coral/20 dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext dark:placeholder:text-creamtext/30";

  return (
    <section
      className={[
        "mb-5 rounded-2xl border p-5 shadow-sm",
        awaiting
          ? "border-molten/40 bg-molten/[0.05] dark:bg-molten/10"
          : "border-plum-800/10 bg-white/70 dark:border-creamtext/10 dark:bg-plum-800",
      ].join(" ")}
    >
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="font-serif text-lg text-plum-800 dark:text-creamtext">
          {awaiting ? "Needs your decision" : "Operator decision"}
        </h3>
        {claim.decided_by && (
          <span className="rounded-full bg-sky/10 dark:bg-sky/20 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-skyText dark:text-sky">
            Decided by {claim.decided_by}
          </span>
        )}
      </div>
      <p className="mb-4 text-xs leading-relaxed text-plum-800/55 dark:text-creamtext/55">
        {awaiting
          ? "The AI routed this claim to manual review. Make the final call — your decision is recorded in the audit trail."
          : "Override the decision if your review differs from the AI. Recorded in the audit trail."}
      </p>

      <div className="flex flex-col gap-3">
        <div role="group" aria-label="Decision" className="grid grid-cols-3 gap-1 rounded-full bg-plum-800/[0.06] p-1 dark:bg-creamtext/10">
          {STATUSES.map((s) => {
            const active = status === s.value;
            return (
              <button
                key={s.value}
                type="button"
                aria-pressed={active}
                onClick={() => setStatus(s.value)}
                className={[
                  "rounded-full px-3 py-1.5 text-sm font-medium transition-colors",
                  active
                    ? "bg-coral text-white shadow-sm"
                    : "text-plum-800/70 hover:text-plum-800 dark:text-creamtext/70 dark:hover:text-creamtext",
                ].join(" ")}
              >
                {s.label}
              </button>
            );
          })}
        </div>

        {status !== "REJECTED" && (
          <div>
            <label htmlFor="decision-amount" className="mb-1.5 block text-xs font-medium text-plum-800/70 dark:text-creamtext/70">
              Approved amount (₹)
            </label>
            <input
              id="decision-amount"
              type="number"
              min={0}
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              className={inputClass}
              placeholder="e.g. 1350"
            />
          </div>
        )}

        <div>
          <label htmlFor="decision-note" className="mb-1.5 block text-xs font-medium text-plum-800/70 dark:text-creamtext/70">
            Reason / note <span className="text-coral">*</span>
          </label>
          <textarea
            id="decision-note"
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className={inputClass}
            placeholder="Why are you making this decision? (recorded in the audit trail)"
          />
        </div>

        {error && (
          <div role="alert" className="rounded-xl border border-crimson/30 bg-crimson/5 px-3.5 py-2.5 text-sm text-crimson">
            {error}
          </div>
        )}

        <button
          type="button"
          onClick={apply}
          disabled={busy || !note.trim()}
          className="inline-flex items-center justify-center gap-2 self-start rounded-full bg-coral px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-plum-800 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-plum-700"
        >
          {busy ? "Recording…" : "Record decision"}
        </button>

        {/* Outcome label: was the AI's automated decision correct? Feeds confidence
            recalibration / conformal risk control (operator-domain labels). */}
        <div className="mt-1 border-t border-plum-800/10 pt-3 dark:border-creamtext/10">
          <p className="mb-2 text-xs font-medium text-plum-800/70 dark:text-creamtext/70">
            Was the AI’s automated decision correct?{" "}
            <span className="font-normal text-plum-800/45 dark:text-creamtext/45">
              (trains confidence calibration)
            </span>
          </p>
          {labeled !== null ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-sky/10 px-3 py-1 text-xs font-semibold text-skyText dark:bg-sky/20 dark:text-sky">
              Marked {labeled ? "correct" : "incorrect"} ✓
            </span>
          ) : (
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => label(true)}
                disabled={labelBusy}
                className="rounded-full border border-emerald-600/40 px-4 py-1.5 text-xs font-semibold text-emerald-700 transition-colors hover:bg-emerald-600/10 disabled:opacity-40 dark:text-emerald-400"
              >
                Correct
              </button>
              <button
                type="button"
                onClick={() => label(false)}
                disabled={labelBusy}
                className="rounded-full border border-crimson/40 px-4 py-1.5 text-xs font-semibold text-crimson transition-colors hover:bg-crimson/10 disabled:opacity-40"
              >
                Incorrect
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
