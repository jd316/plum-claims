// Ops inline field correction panel.
//
// An operator reviewing a claim corrects a low-confidence EXTRACTED field (a
// misread bill total, patient name, diagnosis, or the line-items table). On
// "Apply correction & re-decide" the backend re-runs the DETERMINISTIC decision
// (no Gemini) on the corrected facts, persists the corrected outcome with an
// append-only audit trail, and returns before -> after. We surface that diff
// inline (reusing the StatusPill styling) plus a "corrected by ops" badge and the
// correction history. Gated to ops when auth is ON; open when auth is OFF.

import { useMemo, useState } from "react";

import { correctClaim } from "../api";
import type { FieldCorrection, CorrectionResult } from "../api";
import type { ClaimResult, ExtractionResult } from "../types";
import { useAuth } from "../auth-context";
import StatusPill from "./StatusPill";

// Confidence at/under which a field is highlighted as low-confidence (editable
// fields are always editable; this only drives the visual highlight).
const LOW_CONF = 0.7;

const STR_FIELDS: { key: keyof ExtractionResult; label: string }[] = [
  { key: "patient_name", label: "Patient name" },
  { key: "diagnosis", label: "Diagnosis" },
  { key: "hospital_name", label: "Hospital" },
  { key: "treatment", label: "Treatment" },
];

function inr(n: number | null | undefined): string {
  if (n == null) return "—";
  return `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

// Local editable form state for one extraction document.
interface DocEdits {
  strings: Record<string, string>; // field -> edited value
  total: string; // edited total_amount (string for the input)
  lineItems: { description: string; amount: string }[];
  editTotalMode: "total" | "lines"; // which financial control is active
}

function initEdits(ex: ExtractionResult): DocEdits {
  const strings: Record<string, string> = {};
  for (const f of STR_FIELDS) {
    const sf = ex[f.key] as { value?: string | null } | undefined;
    strings[f.key as string] = sf?.value ?? "";
  }
  return {
    strings,
    total: ex.total_amount?.value != null ? String(ex.total_amount.value) : "",
    lineItems: (ex.line_items ?? []).map((li) => ({
      description: li.description,
      amount: String(li.amount),
    })),
    editTotalMode: "total",
  };
}

export default function OpsCorrectionPanel({
  claim,
  onCorrected,
}: {
  claim: ClaimResult;
  onCorrected?: (updated: Partial<ClaimResult>, res: CorrectionResult) => void;
}) {
  const { enabled, user } = useAuth();
  const isOps = !enabled || user?.role === "ops";

  const extractions = useMemo(() => claim.extractions ?? [], [claim.extractions]);
  const [edits, setEdits] = useState<Record<string, DocEdits>>(() => {
    const m: Record<string, DocEdits> = {};
    for (const ex of extractions) m[ex.file_id] = initEdits(ex);
    return m;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CorrectionResult | null>(null);

  // History from the loaded claim plus any correction applied this session.
  const history = claim.correction_history ?? [];

  // Build the corrections payload by diffing the edited values against the
  // originally-extracted ones. Only changed fields are sent.
  const corrections = useMemo<FieldCorrection[]>(() => {
    const out: FieldCorrection[] = [];
    for (const ex of extractions) {
      const e = edits[ex.file_id];
      if (!e) continue;
      for (const f of STR_FIELDS) {
        const orig = (ex[f.key] as { value?: string | null })?.value ?? "";
        const next = e.strings[f.key as string] ?? "";
        if (next !== orig)
          out.push({ file_id: ex.file_id, field: f.key as string, value: next });
      }
      if (e.editTotalMode === "lines") {
        const origLines = (ex.line_items ?? []).map((li) => ({
          description: li.description,
          amount: li.amount,
        }));
        const nextLines = e.lineItems
          .filter((li) => li.amount.trim() !== "")
          .map((li) => ({
            description: li.description || "Corrected line",
            amount: Number(li.amount),
          }));
        const changed =
          nextLines.length !== origLines.length ||
          nextLines.some(
            (li, i) =>
              li.amount !== origLines[i]?.amount ||
              li.description !== origLines[i]?.description
          );
        if (changed && nextLines.every((li) => !Number.isNaN(li.amount)))
          out.push({ file_id: ex.file_id, field: "line_items", value: nextLines });
      } else {
        const origTotal = ex.total_amount?.value ?? null;
        const nextTotal = e.total.trim() === "" ? null : Number(e.total);
        if (
          nextTotal != null &&
          !Number.isNaN(nextTotal) &&
          nextTotal !== origTotal
        )
          out.push({
            file_id: ex.file_id,
            field: "total_amount",
            value: nextTotal,
          });
      }
    }
    return out;
  }, [edits, extractions]);

  if (!isOps || extractions.length === 0) return null;

  async function apply() {
    if (corrections.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const res = await correctClaim(claim.claim_id, corrections);
      setResult(res);
      onCorrected?.(
        {
          corrected_by: user?.username ?? "ops",
          corrected_at: new Date().toISOString(),
        },
        res
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Correction failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-2xl border border-plum-800/10 dark:border-creamtext/10 bg-white/70 dark:bg-plum-800 p-5 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-serif text-lg text-plum-800 dark:text-creamtext">Ops correction</h3>
        {claim.corrected_by && (
          <span className="rounded-full bg-sky/10 dark:bg-sky/20 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-skyText dark:text-sky">
            Corrected by ops
          </span>
        )}
      </div>
      <p className="mb-4 text-xs text-plum-800/55 dark:text-creamtext/55">
        Correct a misread extracted field and re-run the deterministic decision.
        Low-confidence fields (&lt;{LOW_CONF.toFixed(1)}) are highlighted.
      </p>

      {extractions.map((ex) => {
        const e = edits[ex.file_id];
        if (!e) return null;
        const set = (patch: Partial<DocEdits>) =>
          setEdits((prev) => ({
            ...prev,
            [ex.file_id]: { ...prev[ex.file_id], ...patch },
          }));
        return (
          <div
            key={ex.file_id}
            className="mb-4 rounded-xl border border-plum-800/10 dark:border-creamtext/10 p-4"
          >
            <div className="mb-3 flex items-center gap-2 text-xs text-plum-800/55 dark:text-creamtext/55">
              <span className="font-mono">{ex.file_id}</span>
              <span className="rounded bg-plum-800/[0.06] dark:bg-creamtext/10 px-1.5 py-0.5 font-semibold uppercase tracking-wide">
                {ex.doc_type}
              </span>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {STR_FIELDS.map((f) => {
                const sf = ex[f.key] as { confidence: number } | undefined;
                const low = (sf?.confidence ?? 1) < LOW_CONF;
                return (
                  <label key={f.key as string} className="block text-sm">
                    <span className="mb-1 flex items-center gap-1.5 text-plum-800/70 dark:text-creamtext/70">
                      {f.label}
                      {low && (
                        <span className="rounded bg-sun/20 px-1 py-0.5 text-[10px] font-semibold text-sunText dark:text-sun">
                          {(sf?.confidence ?? 0).toFixed(2)}
                        </span>
                      )}
                    </span>
                    <input
                      className={`w-full rounded-lg border px-3 py-1.5 text-sm outline-none focus:border-coral dark:text-creamtext ${
                        low ? "border-sun/50 bg-sun/[0.04]" : "border-plum-800/15 dark:border-creamtext/15"
                      }`}
                      value={e.strings[f.key as string] ?? ""}
                      onChange={(ev) =>
                        set({
                          strings: {
                            ...e.strings,
                            [f.key as string]: ev.target.value,
                          },
                        })
                      }
                    />
                  </label>
                );
              })}
            </div>

            {/* Financial: total or an editable line-items table. */}
            <div className="mt-4">
              <div className="mb-2 flex items-center gap-3 text-xs">
                <button
                  type="button"
                  onClick={() => set({ editTotalMode: "total" })}
                  className={`rounded-full px-3 py-1 font-semibold ${
                    e.editTotalMode === "total"
                      ? "bg-plum-800 text-white"
                      : "bg-plum-800/[0.06] dark:bg-creamtext/10 text-plum-800/60 dark:text-creamtext/60"
                  }`}
                >
                  Total amount
                </button>
                <button
                  type="button"
                  onClick={() => set({ editTotalMode: "lines" })}
                  className={`rounded-full px-3 py-1 font-semibold ${
                    e.editTotalMode === "lines"
                      ? "bg-plum-800 text-white"
                      : "bg-plum-800/[0.06] dark:bg-creamtext/10 text-plum-800/60 dark:text-creamtext/60"
                  }`}
                >
                  Line items
                </button>
                {(ex.total_amount?.confidence ?? 1) < LOW_CONF && (
                  <span className="rounded bg-sun/20 px-1 py-0.5 text-[10px] font-semibold text-sunText dark:text-sun">
                    total conf {(ex.total_amount?.confidence ?? 0).toFixed(2)}
                  </span>
                )}
              </div>

              {e.editTotalMode === "total" ? (
                <label className="block text-sm">
                  <span className="mb-1 block text-plum-800/70 dark:text-creamtext/70">
                    Bill total (₹)
                  </span>
                  <input
                    type="number"
                    className="w-48 rounded-lg border border-plum-800/15 dark:border-creamtext/15 dark:text-creamtext px-3 py-1.5 text-sm outline-none focus:border-coral"
                    value={e.total}
                    onChange={(ev) => set({ total: ev.target.value })}
                  />
                </label>
              ) : (
                <div className="space-y-2">
                  {e.lineItems.map((li, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <input
                        className="flex-1 rounded-lg border border-plum-800/15 dark:border-creamtext/15 dark:text-creamtext px-2 py-1 text-sm outline-none focus:border-coral"
                        placeholder="Description"
                        value={li.description}
                        onChange={(ev) => {
                          const next = [...e.lineItems];
                          next[i] = { ...next[i], description: ev.target.value };
                          set({ lineItems: next });
                        }}
                      />
                      <input
                        type="number"
                        className="w-28 rounded-lg border border-plum-800/15 dark:border-creamtext/15 dark:text-creamtext px-2 py-1 text-sm outline-none focus:border-coral"
                        placeholder="Amount"
                        value={li.amount}
                        onChange={(ev) => {
                          const next = [...e.lineItems];
                          next[i] = { ...next[i], amount: ev.target.value };
                          set({ lineItems: next });
                        }}
                      />
                      <button
                        type="button"
                        className="text-plum-800/40 dark:text-creamtext/40 hover:text-crimson"
                        onClick={() =>
                          set({
                            lineItems: e.lineItems.filter((_, j) => j !== i),
                          })
                        }
                        aria-label="Remove line"
                      >
                        ✕
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    className="text-xs font-semibold text-coral hover:text-plum-800"
                    onClick={() =>
                      set({
                        lineItems: [
                          ...e.lineItems,
                          { description: "", amount: "" },
                        ],
                      })
                    }
                  >
                    + Add line
                  </button>
                </div>
              )}
            </div>
          </div>
        );
      })}

      {error && (
        <p className="mb-3 rounded-lg bg-crimson/10 dark:bg-crimson/20 px-3 py-2 text-sm text-crimson">
          {error}
        </p>
      )}

      <button
        type="button"
        onClick={apply}
        disabled={busy || corrections.length === 0}
        className="rounded-full bg-coral px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-plum-800 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {busy ? "Re-deciding…" : "Apply correction & re-decide"}
      </button>
      {corrections.length > 0 && !busy && (
        <span className="ml-3 text-xs text-plum-800/55 dark:text-creamtext/55">
          {corrections.length} field
          {corrections.length === 1 ? "" : "s"} changed
        </span>
      )}

      {/* Inline before -> after of the just-applied correction. */}
      {result && (
        <div className="mt-4 rounded-xl border border-plum-800/10 dark:border-creamtext/10 bg-plum-800/[0.02] dark:bg-creamtext/10 p-4">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <div className="flex items-center gap-2">
              <span className="text-plum-800/55 dark:text-creamtext/55">Before</span>
              <StatusPill status={result.before.status} />
              <span className="font-mono">{inr(result.before.amount)}</span>
            </div>
            <span className="text-plum-800/40 dark:text-creamtext/40">→</span>
            <div className="flex items-center gap-2">
              <span className="text-plum-800/55 dark:text-creamtext/55">After</span>
              <StatusPill status={result.after.status} />
              <span className="font-mono">{inr(result.after.amount)}</span>
            </div>
            {result.persisted && (
              <span className="rounded-full bg-growth/12 dark:bg-growth/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-growthText dark:text-growth">
                Persisted
              </span>
            )}
          </div>
          {result.changed_rules.length > 0 && (
            <ul className="mt-3 space-y-1 text-xs text-plum-800/60 dark:text-creamtext/60">
              {result.changed_rules.map((r, i) => (
                <li key={i}>
                  <span className="font-semibold">{r.rule}</span>:{" "}
                  {r.before.status}
                  {r.before.reason_code ? ` (${r.before.reason_code})` : ""} →{" "}
                  {r.after.status}
                  {r.after.reason_code ? ` (${r.after.reason_code})` : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Append-only correction history from the stored claim. */}
      {history.length > 0 && (
        <div className="mt-5">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-plum-800/45 dark:text-creamtext/45">
            Correction history
          </h4>
          <ul className="space-y-2">
            {history.map((h, i) => (
              <li
                key={i}
                className="rounded-lg border border-plum-800/10 dark:border-creamtext/10 px-3 py-2 text-xs text-plum-800/65 dark:text-creamtext/65"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{h.corrected_by}</span>
                  <span className="text-plum-800/40 dark:text-creamtext/40">
                    {new Date(h.corrected_at).toLocaleString()}
                  </span>
                  <StatusPill status={h.before.status} />
                  <span className="text-plum-800/40 dark:text-creamtext/40">→</span>
                  <StatusPill status={h.after.status} />
                  <span className="font-mono">
                    {inr(h.before.amount)} → {inr(h.after.amount)}
                  </span>
                </div>
                {Array.isArray(h.changed_fields) &&
                  h.changed_fields.length > 0 && (
                    <div className="mt-1 text-plum-800/45 dark:text-creamtext/45">
                      fields:{" "}
                      {h.changed_fields
                        .map((c) => (c as { field?: string }).field)
                        .filter(Boolean)
                        .join(", ")}
                    </div>
                  )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
