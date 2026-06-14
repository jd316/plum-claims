import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  activatePolicyVersion,
  createPolicyVersion,
  getCurrentPolicy,
  getEvalCases,
  listPolicyVersions,
  policyDiff,
  previewPolicy,
  type PolicyDiff,
  type PolicyPreview,
  type PolicyVersionFull,
  type PolicyVersionMeta,
} from "../api";
import type { EvalCase } from "../types";
import { formatRupees } from "../utils/format";

// ── Categories with decision-relevant knobs (a structured subset of opd_categories) ──
const CATEGORIES = [
  "consultation",
  "diagnostic",
  "pharmacy",
  "dental",
  "vision",
  "alternative_medicine",
] as const;

const CATEGORY_LABEL: Record<string, string> = {
  consultation: "Consultation",
  diagnostic: "Diagnostic",
  pharmacy: "Pharmacy",
  dental: "Dental",
  vision: "Vision",
  alternative_medicine: "Alternative medicine",
};

// Column-header labels for the category-rule inputs — also used as each input's
// accessible name (screen readers otherwise announce only "spin button").
const COLUMN_LABEL: Record<string, string> = {
  sub_limit: "Sub-limit ₹",
  copay_percent: "Co-pay %",
  network_discount_percent: "Network discount %",
};

function num(v: unknown): number {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(n) ? n : 0;
}

// Deep clone — the editable working copy must never alias the loaded active policy.
function clone<T>(o: T): T {
  return JSON.parse(JSON.stringify(o));
}

// ── Status pill (mirrors the rest of the app's palette) ──
const STATUS_PILL: Record<string, { bg: string; text: string }> = {
  APPROVED: { bg: "bg-growth/12 dark:bg-growth/20", text: "text-growthText dark:text-growth" },
  PARTIAL: { bg: "bg-sun/15 dark:bg-sun/20", text: "text-sunText dark:text-sun" },
  REJECTED: { bg: "bg-crimson/10 dark:bg-crimson/20", text: "text-crimson" },
  MANUAL_REVIEW: { bg: "bg-sky/10 dark:bg-sky/20", text: "text-skyText dark:text-sky" },
};

function StatusPill({ status }: { status: string }) {
  const meta = STATUS_PILL[status] ?? { bg: "bg-plum-800/[0.07] dark:bg-creamtext/10", text: "text-plum-800/55 dark:text-creamtext/55" };
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${meta.bg} ${meta.text}`}
    >
      {status}
    </span>
  );
}

function NumberField({
  label,
  value,
  onChange,
  suffix,
}: {
  label: string;
  value: number;
  onChange: (n: number) => void;
  suffix?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-semibold uppercase tracking-[0.1em] text-plum-800/40 dark:text-creamtext/40">
        {label}
      </span>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={value}
          onChange={(e) => onChange(num(e.target.value))}
          className="w-full rounded-lg border border-plum-800/15 bg-white px-3 py-1.5 font-mono text-sm text-plum-800 focus:border-coral focus:outline-none dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext"
        />
        {suffix && <span className="text-[12px] text-plum-800/45 dark:text-creamtext/45">{suffix}</span>}
      </div>
    </label>
  );
}

// The editable policy document. The decision-relevant knobs the studio edits are
// typed; `[k: string]: unknown` keeps the rest of the policy JSON intact so the
// draft round-trips faithfully (the Raw-JSON panel can carry any other key).
interface PolicyDraft {
  coverage: Record<string, number>;
  opd_categories: Record<string, Record<string, number>>;
  waiting_periods: { specific_conditions?: Record<string, number>; [k: string]: unknown };
  exclusions: { conditions?: string[]; [k: string]: unknown };
  [k: string]: unknown;
}

export default function PolicyStudio() {
  const [active, setActive] = useState<PolicyVersionFull | null>(null);
  const [draft, setDraft] = useState<PolicyDraft | null>(null);
  const [versions, setVersions] = useState<PolicyVersionMeta[]>([]);
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Preview state
  const [sampleId, setSampleId] = useState<string>("TC004");
  const [preview, setPreview] = useState<PolicyPreview | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // Save / activate state
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);

  // Diff state
  const [diff, setDiff] = useState<PolicyDiff | null>(null);
  const [rawMode, setRawMode] = useState(false);
  const [rawText, setRawText] = useState("");
  const [rawError, setRawError] = useState<string | null>(null);

  async function reload() {
    try {
      const [cur, vers] = await Promise.all([getCurrentPolicy(), listPolicyVersions()]);
      setActive(cur);
      setDraft(clone(cur.policy_json) as PolicyDraft);
      setRawText(JSON.stringify(cur.policy_json, null, 2));
      setVersions(vers);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load policy.");
    }
  }

  useEffect(() => {
    // Initial data load — state is set inside async continuations, never
    // synchronously during the effect.
    void (async () => {
      await reload();
    })();
    getEvalCases()
      .then(setCases)
      .catch(() => void 0);
  }, []);

  // Mutate a nested path in the draft immutably.
  function setCat(cat: string, key: string, value: number) {
    setDraft((d) => {
      if (!d) return d;
      const next = clone(d);
      next.opd_categories[cat] = { ...next.opd_categories[cat], [key]: value };
      return next;
    });
  }

  function setCoverage(key: string, value: number) {
    setDraft((d) => {
      if (!d) return d;
      const next = clone(d);
      next.coverage = { ...next.coverage, [key]: value };
      return next;
    });
  }

  function setWaiting(condition: string, value: number) {
    setDraft((d) => {
      if (!d) return d;
      const next = clone(d);
      next.waiting_periods.specific_conditions = {
        ...next.waiting_periods.specific_conditions,
        [condition]: value,
      };
      return next;
    });
  }

  function setExclusions(text: string) {
    setDraft((d) => {
      if (!d) return d;
      const next = clone(d);
      next.exclusions = {
        ...next.exclusions,
        conditions: text
          .split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      };
      return next;
    });
  }

  // Apply the raw-JSON textarea into the draft (validates JSON parse).
  function applyRaw() {
    try {
      const parsed = JSON.parse(rawText);
      setDraft(parsed);
      setRawError(null);
      setNotice("Raw JSON applied to the working copy.");
    } catch (e) {
      setRawError(e instanceof Error ? e.message : "Invalid JSON.");
    }
  }

  // Whether the draft differs from the loaded active policy.
  const dirty = useMemo(() => {
    if (!active || !draft) return false;
    return JSON.stringify(active.policy_json) !== JSON.stringify(draft);
  }, [active, draft]);

  async function handlePreview() {
    if (!draft) return;
    setPreviewing(true);
    setError(null);
    try {
      const p = await previewPolicy(draft, { test_case_id: sampleId });
      setPreview(p);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preview failed.");
    } finally {
      setPreviewing(false);
    }
  }

  async function handleSave() {
    if (!draft) return;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const row = await createPolicyVersion(
        draft,
        label.trim() || `Edited ${new Date().toLocaleString()}`
      );
      setNotice(`Saved as v${row.version_no} (inactive). Activate it from the history below to go live.`);
      setLabel("");
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleActivate(v: PolicyVersionMeta) {
    const ok = window.confirm(
      `Activate v${v.version_no}? This rewrites the live policy and CHANGES LIVE DECISIONS for every new claim.`
    );
    if (!ok) return;
    setError(null);
    try {
      await activatePolicyVersion(v.id);
      setNotice(`Activated v${v.version_no}. It is now the live policy.`);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Activate failed.");
    }
  }

  async function handleDiff(v: PolicyVersionMeta) {
    if (!active) return;
    setError(null);
    try {
      const d = await policyDiff(active.id, v.id);
      setDiff(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Diff failed.");
    }
  }

  if (!draft) {
    return (
      <div className="mx-auto max-w-content px-6 py-12">
        {error ? (
          <div className="rounded-xl border border-crimson/30 bg-crimson/5 dark:bg-crimson/20 px-4 py-3 text-sm text-crimson">
            {error}
          </div>
        ) : (
          <p className="text-sm text-plum-800/50 dark:text-creamtext/50">Loading policy…</p>
        )}
      </div>
    );
  }

  const cov: Record<string, number> = draft.coverage ?? {};
  const waiting: Record<string, number> =
    draft.waiting_periods?.specific_conditions ?? {};

  return (
    <div className="mx-auto max-w-content px-6 py-10">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-serif text-4xl text-plum-800 dark:text-creamtext">Policy studio</h1>
          <p className="mt-1 text-[14px] text-plum-800/55 dark:text-creamtext/55">
            Edit the policy, preview the impact on a real claim, version it, and activate —
            no deploy. Active:{" "}
            <span className="font-semibold text-plum-800 dark:text-creamtext">
              v{active?.version_no} {active?.label ? `· ${active.label}` : ""}
            </span>
          </p>
        </div>
        <nav className="flex gap-2 text-sm">
          <Link
            to="/ops"
            className="rounded-full border border-plum-800/20 px-4 py-2 font-semibold text-plum-800 transition-colors hover:bg-plum-800/5 dark:border-creamtext/10 dark:text-creamtext dark:hover:bg-creamtext/10"
          >
            Ops dashboard
          </Link>
        </nav>
      </header>

      {error && (
        <div className="mb-5 rounded-xl border border-crimson/30 bg-crimson/5 dark:bg-crimson/20 px-4 py-3 text-sm text-crimson">
          {error}
        </div>
      )}
      {notice && (
        <div className="mb-5 rounded-xl border border-growth/30 bg-growth/[0.06] dark:bg-growth/20 px-4 py-3 text-sm text-growthText dark:text-growth">
          {notice}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ── Editor ── */}
        <section className="lg:col-span-2 flex flex-col gap-5">
          {/* Coverage limits */}
          <div className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
            <h2 className="mb-3 font-serif text-xl text-plum-800 dark:text-creamtext">Coverage limits</h2>
            <div className="grid gap-4 sm:grid-cols-2">
              <NumberField
                label="Per-claim limit"
                value={num(cov.per_claim_limit)}
                onChange={(n) => setCoverage("per_claim_limit", n)}
                suffix="₹"
              />
              <NumberField
                label="Annual OPD limit"
                value={num(cov.annual_opd_limit)}
                onChange={(n) => setCoverage("annual_opd_limit", n)}
                suffix="₹"
              />
            </div>
          </div>

          {/* Category knobs */}
          <div className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
            <h2 className="mb-3 font-serif text-xl text-plum-800 dark:text-creamtext">Category rules</h2>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-plum-800/40 dark:text-creamtext/40">
                    <th className="py-2 pr-3 font-semibold">Category</th>
                    <th className="py-2 px-2 font-semibold">Sub-limit ₹</th>
                    <th className="py-2 px-2 font-semibold">Co-pay %</th>
                    <th className="py-2 px-2 font-semibold">Network disc. %</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-plum-800/[0.06] dark:divide-creamtext/10">
                  {CATEGORIES.map((cat) => {
                    const c: Record<string, number> =
                      draft.opd_categories?.[cat] ?? {};
                    return (
                      <tr key={cat}>
                        <td className="py-2 pr-3 text-plum-800/75 dark:text-creamtext/75">{CATEGORY_LABEL[cat]}</td>
                        {(["sub_limit", "copay_percent", "network_discount_percent"] as const).map(
                          (k) => (
                            <td key={k} className="py-1.5 px-1.5">
                              <input
                                type="number"
                                value={num(c[k])}
                                onChange={(e) => setCat(cat, k, num(e.target.value))}
                                aria-label={`${CATEGORY_LABEL[cat]} — ${COLUMN_LABEL[k]}`}
                                className="w-24 rounded-lg border border-plum-800/15 bg-white px-2 py-1 font-mono text-[13px] text-plum-800 focus:border-coral focus:outline-none dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext"
                              />
                            </td>
                          )
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Waiting periods */}
          <div className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
            <h2 className="mb-3 font-serif text-xl text-plum-800 dark:text-creamtext">
              Waiting periods (days)
            </h2>
            <div className="grid gap-3 sm:grid-cols-3">
              {Object.keys(waiting).map((cond) => (
                <NumberField
                  key={cond}
                  label={cond.replace(/_/g, " ")}
                  value={num(waiting[cond])}
                  onChange={(n) => setWaiting(cond, n)}
                  suffix="d"
                />
              ))}
            </div>
          </div>

          {/* Exclusions */}
          <div className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
            <h2 className="mb-2 font-serif text-xl text-plum-800 dark:text-creamtext">Exclusions</h2>
            <p className="mb-2 text-[12px] text-plum-800/45 dark:text-creamtext/45">One condition per line.</p>
            <textarea
              rows={6}
              aria-label="Policy exclusions — one condition per line"
              value={(draft.exclusions?.conditions ?? []).join("\n")}
              onChange={(e) => setExclusions(e.target.value)}
              className="w-full rounded-lg border border-plum-800/15 bg-white px-3 py-2 font-mono text-[13px] text-plum-800 focus:border-coral focus:outline-none dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext"
            />
          </div>

          {/* Raw JSON fallback */}
          <div className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
            <button
              type="button"
              onClick={() => setRawMode((v) => !v)}
              aria-expanded={rawMode}
              className="text-sm font-semibold text-plum-800/70 hover:text-coral dark:text-creamtext/70"
            >
              <span aria-hidden>{rawMode ? "▾" : "▸"}</span> Raw JSON (advanced — every other field)
            </button>
            {rawMode && (
              <div className="mt-3">
                <textarea
                  rows={16}
                  aria-label="Raw policy JSON"
                  value={rawText}
                  onChange={(e) => setRawText(e.target.value)}
                  className="w-full rounded-lg border border-plum-800/15 bg-plum-800/[0.02] px-3 py-2 font-mono text-[12px] text-plum-800 focus:border-coral focus:outline-none dark:border-creamtext/15 dark:bg-plum-900 dark:text-creamtext"
                />
                {rawError && (
                  <p className="mt-1 text-[12px] text-crimson">{rawError}</p>
                )}
                <button
                  type="button"
                  onClick={applyRaw}
                  className="mt-2 rounded-full bg-plum-800 px-4 py-1.5 text-[13px] font-semibold text-creamtext hover:bg-coral"
                >
                  Apply raw JSON to working copy
                </button>
              </div>
            )}
          </div>
        </section>

        {/* ── Right rail: preview + save ── */}
        <section className="flex flex-col gap-5">
          {/* Preview impact */}
          <div className="rounded-card border border-coral/30 bg-coral/[0.04] p-5 shadow-sm dark:bg-plum-800">
            <h2 className="font-serif text-xl text-plum-800 dark:text-creamtext">Preview impact</h2>
            <p className="mt-1 text-[12px] text-plum-800/55 dark:text-creamtext/55">
              Run a sample claim under your edits vs the live policy. Read-only — never
              changes the active policy.
            </p>
            <div className="mt-3 flex flex-col gap-2">
              <select
                value={sampleId}
                onChange={(e) => setSampleId(e.target.value)}
                aria-label="Sample claim for impact preview"
                className="rounded-lg border border-plum-800/15 bg-white px-3 py-1.5 text-sm text-plum-800 focus:border-coral focus:outline-none dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext"
              >
                {cases.map((c) => (
                  <option key={c.case_id} value={c.case_id}>
                    {c.case_id} — {c.case_name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={handlePreview}
                disabled={previewing}
                className="rounded-full bg-coral px-5 py-2 text-sm font-semibold text-white transition-colors hover:bg-plum-800 disabled:opacity-60"
              >
                {previewing ? "Previewing…" : "Preview impact"}
              </button>
            </div>

            {preview && (
              <div className="mt-4 grid grid-cols-2 gap-3">
                {(["before", "after"] as const).map((side) => {
                  const d = preview[side];
                  return (
                    <div
                      key={side}
                      className={`rounded-xl border p-3 ${
                        side === "after" && preview.changed
                          ? "border-coral/40 bg-white dark:bg-plum-800"
                          : "border-plum-800/10 bg-white dark:border-creamtext/10 dark:bg-plum-800"
                      }`}
                    >
                      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-plum-800/40 dark:text-creamtext/40">
                        {side === "before" ? "Active policy" : "Your edits"}
                      </p>
                      <div className="mt-1.5">
                        <StatusPill status={d.status} />
                      </div>
                      <p className="mt-2 font-mono text-lg text-plum-800 dark:text-creamtext">
                        {formatRupees(d.approved_amount)}
                      </p>
                    </div>
                  );
                })}
                {preview.changed && (
                  <p className="col-span-2 text-[12px] font-medium text-coral">
                    This change moves {preview.sample.label} from{" "}
                    {formatRupees(preview.before.approved_amount)} to{" "}
                    {formatRupees(preview.after.approved_amount)}.
                  </p>
                )}
                {!preview.changed && (
                  <p className="col-span-2 text-[12px] text-plum-800/50 dark:text-creamtext/50">
                    No change for this sample.
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Save new version */}
          <div className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
            <h2 className="font-serif text-xl text-plum-800 dark:text-creamtext">Save version</h2>
            <p className="mt-1 text-[12px] text-plum-800/55 dark:text-creamtext/55">
              Stores an inactive version. Activation is a separate, explicit step.
            </p>
            <input
              type="text"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="Label (e.g. consultation copay 20%)"
              className="mt-3 w-full rounded-lg border border-plum-800/15 bg-white px-3 py-1.5 text-sm text-plum-800 focus:border-coral focus:outline-none dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext"
            />
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !dirty}
              className="mt-2 w-full rounded-full bg-plum-800 px-5 py-2 text-sm font-semibold text-creamtext transition-colors hover:bg-coral disabled:opacity-50"
            >
              {saving ? "Saving…" : dirty ? "Save as new version" : "No changes to save"}
            </button>
          </div>

          {/* Version history */}
          <div className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
            <h2 className="mb-3 font-serif text-xl text-plum-800 dark:text-creamtext">Version history</h2>
            <ul className="flex flex-col gap-2">
              {versions.map((v) => (
                <li
                  key={v.id}
                  className="flex items-center justify-between rounded-xl border border-plum-800/[0.08] px-3 py-2 dark:border-creamtext/10"
                >
                  <div>
                    <p className="text-sm font-semibold text-plum-800 dark:text-creamtext">
                      v{v.version_no}
                      {v.is_active && (
                        <span className="ml-2 rounded-full bg-growth/12 px-2 py-0.5 text-[10px] font-semibold uppercase text-growthText dark:bg-growth/20 dark:text-growth">
                          active
                        </span>
                      )}
                    </p>
                    <p className="text-[11px] text-plum-800/45 dark:text-creamtext/45">{v.label ?? "—"}</p>
                  </div>
                  <div className="flex gap-1.5">
                    {active && v.id !== active.id && (
                      <button
                        type="button"
                        onClick={() => handleDiff(v)}
                        className="rounded-full border border-plum-800/20 px-3 py-1 text-[12px] font-semibold text-plum-800/70 hover:bg-plum-800/5 dark:border-creamtext/10 dark:text-creamtext/70 dark:hover:bg-creamtext/10"
                      >
                        Diff
                      </button>
                    )}
                    {!v.is_active && (
                      <button
                        type="button"
                        onClick={() => handleActivate(v)}
                        className="rounded-full bg-coral px-3 py-1 text-[12px] font-semibold text-white hover:bg-plum-800"
                      >
                        Activate
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>

          {/* Diff view */}
          {diff && (
            <div className="rounded-card border border-plum-800/[0.1] bg-white p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="font-serif text-xl text-plum-800 dark:text-creamtext">
                  Diff v{diff.a.version_no} → v{diff.b.version_no}
                </h2>
                <button
                  type="button"
                  onClick={() => setDiff(null)}
                  className="text-[12px] text-plum-800/50 hover:text-coral dark:text-creamtext/50"
                >
                  Close
                </button>
              </div>
              {diff.changes.length === 0 ? (
                <p className="text-sm text-plum-800/50 dark:text-creamtext/50">No differences.</p>
              ) : (
                <ul className="flex flex-col gap-1.5 font-mono text-[12px]">
                  {diff.changes.map((c) => (
                    <li key={c.path} className="flex flex-col">
                      <span className="text-plum-800/60 dark:text-creamtext/60">{c.path}</span>
                      <span>
                        <span className="text-crimson">{JSON.stringify(c.before)}</span>
                        {" → "}
                        <span className="text-growthText">{JSON.stringify(c.after)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
