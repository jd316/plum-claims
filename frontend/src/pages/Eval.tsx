import { useEffect, useRef, useState } from "react";

import { getEvalCases, gradeMessageQuality, runEval } from "../api";
import type { MessageQualityResult } from "../api";
import type { EvalCase, EvalCaseResult } from "../types";
import TraceTimeline from "../components/TraceTimeline";
import { formatRupees } from "../utils/format";
import { useAuth } from "../auth-context";

// ── Status pill helpers ──────────────────────────────────────────────────────

const STATUS_PILL: Record<string, { bg: string; text: string; label: string }> =
  {
    APPROVED: { bg: "bg-growth/12 dark:bg-growth/20", text: "text-growthText dark:text-growth", label: "Approved" },
    PARTIAL: { bg: "bg-sun/15 dark:bg-sun/20", text: "text-sunText dark:text-sun", label: "Partial" },
    REJECTED: { bg: "bg-crimson/10 dark:bg-crimson/20", text: "text-crimson", label: "Rejected" },
    MANUAL_REVIEW: {
      bg: "bg-sky/10 dark:bg-sky/20",
      text: "text-skyText dark:text-sky",
      label: "Manual Review",
    },
    BLOCKED: { bg: "bg-molten/10", text: "text-moltenText dark:text-molten", label: "Blocked" },
  };

function StatusPill({ status }: { status: string }) {
  const meta = STATUS_PILL[status] ?? {
    bg: "bg-plum-800/[0.07] dark:bg-plum-700",
    text: "text-plum-800/55 dark:text-creamtext/55",
    label: status,
  };
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${meta.bg} ${meta.text}`}
    >
      {meta.label}
    </span>
  );
}

// ── Expected decision helper ─────────────────────────────────────────────────

function expectedDecision(c: EvalCase): string {
  const exp = c.expected as Record<string, unknown>;
  if (exp.blocked === true || exp.decision === null || exp.decision === undefined) {
    // Check if blocked is explicitly true, or decision is null/missing (blocked case)
    if (exp.blocked === true) return "BLOCKED";
    if ("decision" in exp && exp.decision === null) return "BLOCKED";
  }
  const dec = exp.decision as Record<string, unknown> | null | undefined;
  if (dec && typeof dec.status === "string") return dec.status;
  // Fallback: look for top-level status key
  if (typeof exp.status === "string") return exp.status;
  return "—";
}

// ── Spinner ──────────────────────────────────────────────────────────────────

function Spinner() {
  return (
    <svg
      className="h-5 w-5 animate-spin text-coral"
      fill="none"
      viewBox="0 0 24 24"
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
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

// ── Legend ───────────────────────────────────────────────────────────────────

const LEGEND_ITEMS = [
  { key: "APPROVED", label: "Approved" },
  { key: "PARTIAL", label: "Partial" },
  { key: "REJECTED", label: "Rejected" },
  { key: "MANUAL_REVIEW", label: "Manual Review" },
  { key: "BLOCKED", label: "Blocked" },
] as const;

function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
      {LEGEND_ITEMS.map(({ key, label }) => {
        const meta = STATUS_PILL[key];
        return (
          <span key={key} className="flex items-center gap-1.5">
            <span
              className={`inline-block h-2.5 w-2.5 rounded-full ${meta.bg} ${meta.text} border border-current`}
            />
            <span className="text-[12px] text-plum-800/60 dark:text-creamtext/60">{label}</span>
          </span>
        );
      })}
      <span className="flex items-center gap-1.5">
        <span className="text-base leading-none">✅</span>
        <span className="text-[12px] text-plum-800/60 dark:text-creamtext/60">Matched</span>
      </span>
      <span className="flex items-center gap-1.5">
        <span className="text-base leading-none">❌</span>
        <span className="text-[12px] text-plum-800/60 dark:text-creamtext/60">Mismatch</span>
      </span>
    </div>
  );
}

// ── Result row (expandable) ──────────────────────────────────────────────────

function ResultRow({
  row,
  expectedCase,
}: {
  row: EvalCaseResult;
  expectedCase: EvalCase | undefined;
}) {
  const [open, setOpen] = useState(false);

  const producedStatus = row.result.blocked
    ? "BLOCKED"
    : (row.result.decision?.status ?? "—");

  const expectedStatus = expectedCase ? expectedDecision(expectedCase) : "—";

  const approvedAmount =
    !row.result.blocked && row.result.decision?.approved_amount != null
      ? row.result.decision.approved_amount
      : null;

  return (
    <>
      <tr
        className={`group cursor-pointer transition-colors hover:bg-cream/70 dark:hover:bg-creamtext/5 ${
          open ? "bg-cream/50 dark:bg-plum-900" : ""
        }`}
        onClick={() => setOpen((v) => !v)}
      >
        {/* case_id */}
        <td className="whitespace-nowrap px-5 py-3.5">
          <span className="font-mono text-[12px] text-plum-800/60 dark:text-creamtext/60">
            {row.case_id}
          </span>
        </td>

        {/* case_name */}
        <td className="px-4 py-3.5 text-sm font-medium text-plum-800 dark:text-creamtext">
          {row.case_name}
        </td>

        {/* expected */}
        <td className="px-4 py-3.5">
          <StatusPill status={expectedStatus} />
        </td>

        {/* produced */}
        <td className="px-4 py-3.5">
          <StatusPill status={producedStatus} />
        </td>

        {/* approved amount */}
        <td className="whitespace-nowrap px-4 py-3.5 text-right font-mono text-[13px] text-plum-800/75 dark:text-creamtext/75">
          {approvedAmount != null ? formatRupees(approvedAmount) : "—"}
        </td>

        {/* match */}
        <td className="px-4 py-3.5 text-center text-base">
          {row.matched ? "✅" : "❌"}
        </td>

        {/* expand chevron */}
        <td className="px-4 py-3.5 text-right">
          <svg
            className={`ml-auto h-4 w-4 text-plum-800/35 dark:text-creamtext/35 transition-transform ${
              open ? "rotate-180" : ""
            }`}
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={2}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="m19.5 8.25-7.5 7.5-7.5-7.5"
            />
          </svg>
        </td>
      </tr>

      {/* mismatch notes row */}
      {!row.matched && row.notes.length > 0 && (
        <tr className="bg-crimson/[0.03] dark:bg-crimson/20">
          <td colSpan={7} className="px-5 pb-2 pt-0">
            <ul className="flex flex-col gap-0.5">
              {row.notes.map((note, i) => (
                <li
                  key={i}
                  className="flex items-start gap-2 text-[12px] text-crimson"
                >
                  <span className="mt-0.5 flex-shrink-0">↳</span>
                  <span>{note}</span>
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}

      {/* expanded detail */}
      {open && (
        <tr>
          <td colSpan={7} className="bg-cream/60 dark:bg-plum-900 px-5 pb-6 pt-2">
            <div className="flex flex-col gap-4">
              {/* blocked problems */}
              {row.result.blocked && row.result.problems.length > 0 && (
                <div className="rounded-xl border border-molten/25 bg-molten/[0.06] px-4 py-4">
                  <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-molten">
                    Blocked — Problems
                  </p>
                  <ul className="flex flex-col gap-1.5">
                    {row.result.problems.map((p, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-2 text-[13px] text-molten/90"
                      >
                        <span className="mt-0.5 flex-shrink-0 font-mono text-[11px] font-semibold uppercase">
                          [{p.kind}]
                        </span>
                        <span>{p.message}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* decision summary */}
              {!row.result.blocked && row.result.decision && (
                <div className="rounded-xl border border-plum-800/[0.1] dark:border-creamtext/10 bg-white dark:bg-plum-800 px-4 py-4">
                  <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-plum-800/40 dark:text-creamtext/40">
                    Decision Summary
                  </p>
                  <div className="flex flex-col gap-1.5 text-sm text-plum-800/80 dark:text-creamtext/80">
                    <div>
                      <span className="font-semibold">Status: </span>
                      {row.result.decision.status}
                      {row.result.decision.approved_amount > 0 && (
                        <span className="ml-2 text-plum-800/60 dark:text-creamtext/60">
                          · {formatRupees(row.result.decision.approved_amount)}{" "}
                          approved
                        </span>
                      )}
                    </div>
                    {row.result.decision.member_message && (
                      <div className="text-[13px] italic text-plum-800/60 dark:text-creamtext/60">
                        "{row.result.decision.member_message}"
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* trace */}
              {row.result.trace.length > 0 && (
                <TraceTimeline trace={row.result.trace} />
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Message-quality results card ─────────────────────────────────────────────

const MQ_DIMENSIONS = [
  { key: "specificity", label: "Specificity" },
  { key: "actionability", label: "Actionability" },
  { key: "correctness", label: "Correctness" },
  { key: "tone", label: "Tone" },
  { key: "jargon_free", label: "Jargon-free" },
] as const;

function MessageQualityCard({ data }: { data: MessageQualityResult }) {
  const agg = data.aggregate;
  return (
    <div className="overflow-hidden rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-plum-800/[0.08] dark:border-creamtext/10 px-6 py-5">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-plum-800/40 dark:text-creamtext/40">
            Message quality (LLM-as-judge)
          </p>
          <p className="mt-1 font-serif text-2xl text-plum-800 dark:text-creamtext">
            Overall {agg.overall.toFixed(2)}
            <span className="text-base text-plum-800/35 dark:text-creamtext/35"> / 5</span>
          </p>
        </div>
        <p className="text-[12px] text-plum-800/50 dark:text-creamtext/50">
          Graded {data.n} of {data.n_total} cases
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 px-6 py-5 sm:grid-cols-5">
        {MQ_DIMENSIONS.map(({ key, label }) => (
          <div key={key}>
            <dt className="text-[11px] font-semibold uppercase tracking-wide text-plum-800/40 dark:text-creamtext/40">
              {label}
            </dt>
            <dd className="mt-0.5 font-serif text-xl text-plum-800 dark:text-creamtext">
              {(agg[key] ?? 0).toFixed(2)}
              <span className="text-sm text-plum-800/30 dark:text-creamtext/30"> / 5</span>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function Eval() {
  const { enabled, user } = useAuth();
  const isOps = !enabled || user?.role === "ops";

  // Message-quality LLM eval — separate from the main 12-case decision run.
  const [mqRunning, setMqRunning] = useState(false);
  const [mqError, setMqError] = useState<string | null>(null);
  const [mqResult, setMqResult] = useState<MessageQualityResult | null>(null);
  const [cases, setCases] = useState<EvalCase[] | null>(null);
  const [casesError, setCasesError] = useState<string | null>(null);

  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [results, setResults] = useState<EvalCaseResult[] | null>(null);

  const isMounted = useRef(true);
  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  // Load case definitions on mount
  useEffect(() => {
    let active = true;
    getEvalCases()
      .then((c) => {
        if (active) setCases(c);
      })
      .catch((err: unknown) => {
        if (active)
          setCasesError(
            err instanceof Error ? err.message : "Failed to load eval cases."
          );
      });
    return () => {
      active = false;
    };
  }, []);

  async function handleRun() {
    setRunning(true);
    setRunError(null);
    try {
      const rows = await runEval();
      if (!isMounted.current) return;
      setResults(rows);
    } catch (err: unknown) {
      if (!isMounted.current) return;
      setRunError(
        err instanceof Error ? err.message : "Eval run failed unexpectedly."
      );
    } finally {
      if (isMounted.current) setRunning(false);
    }
  }

  async function handleGradeMessages() {
    setMqRunning(true);
    setMqError(null);
    try {
      const res = await gradeMessageQuality();
      if (!isMounted.current) return;
      setMqResult(res);
    } catch (err: unknown) {
      if (!isMounted.current) return;
      setMqError(
        err instanceof Error ? err.message : "Message-quality grading failed."
      );
    } finally {
      if (isMounted.current) setMqRunning(false);
    }
  }

  // Summary stats
  const matchCount = results ? results.filter((r) => r.matched).length : 0;
  const total = results?.length ?? cases?.length ?? 12;
  const hasResults = !!results && results.length > 0;
  const matchRate = hasResults ? Math.round((matchCount / total) * 100) : 0;
  const matchColor =
    !hasResults
      ? ""
      : matchCount === total
        ? "text-growth"
        : matchCount >= total * 0.75
          ? "text-sun"
          : "text-crimson";

  return (
    <div className="mx-auto max-w-content px-6 py-12 sm:py-16">
      {/* ── Page header ── */}
      <header className="mb-10">
        <h1 className="font-serif text-4xl text-plum-800 dark:text-creamtext sm:text-5xl">
          Evaluation — {cases?.length ?? 12} test cases
        </h1>
        <p className="mt-3 max-w-2xl text-[15px] leading-relaxed text-plum-800/60 dark:text-creamtext/60">
          Runs the full live AI pipeline across all 12 assignment test cases —
          each goes through document validation, policy rules, financial
          calculation, and the LLM decision agents — then compares the produced
          outcome against the expected result. Takes 5–8 minutes; keep this tab
          open.
        </p>

        <div className="mt-6 flex flex-wrap items-center gap-4">
          <button
            type="button"
            onClick={handleRun}
            disabled={running}
            className="inline-flex items-center gap-2.5 rounded-full bg-coral px-6 py-2.5 text-sm font-semibold text-white shadow-sm transition-all hover:bg-plum-800 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {running && <Spinner />}
            {running
              ? "Running…"
              : `Run all ${cases?.length ?? 12} cases (live)`}
          </button>

          {isOps && (
            <button
              type="button"
              onClick={handleGradeMessages}
              disabled={mqRunning}
              className="inline-flex items-center gap-2.5 rounded-full border border-plum-800/20 dark:border-creamtext/10 bg-white dark:bg-plum-800 px-6 py-2.5 text-sm font-semibold text-plum-800 dark:text-creamtext shadow-sm transition-all hover:border-coral hover:text-coral disabled:cursor-not-allowed disabled:opacity-60"
            >
              {mqRunning && <Spinner />}
              {mqRunning ? "Grading…" : "Grade message quality (LLM)"}
            </button>
          )}

          {results && !running && (
            <span className="text-[13px] text-plum-800/50 dark:text-creamtext/50">
              Last run complete.
            </span>
          )}
        </div>

        {/* Message-quality LLM eval — separate, live, ops-only when auth is on. */}
        {isOps && (mqRunning || mqError || mqResult) && (
          <div className="mt-5 space-y-4">
            {mqRunning && (
              <div className="flex items-start gap-3 rounded-xl border border-sky/40 bg-sky/[0.06] px-4 py-3.5 text-sm text-plum-800/80 dark:text-creamtext/80">
                <span className="mt-0.5 flex-shrink-0">
                  <Spinner />
                </span>
                <div>
                  <p className="font-semibold text-plum-800 dark:text-creamtext">
                    Grading member-facing messages with an LLM judge…
                  </p>
                  <p className="mt-0.5 text-[13px] text-plum-800/60 dark:text-creamtext/60">
                    A live call — roughly 12 judge invocations, about 1–2 minutes.
                    Scores each message on specificity, actionability, correctness,
                    tone, and jargon-free language.
                  </p>
                </div>
              </div>
            )}
            {mqError && (
              <div className="rounded-xl border border-crimson/30 bg-crimson/5 dark:bg-crimson/20 px-4 py-3 text-sm text-crimson">
                {mqError}
              </div>
            )}
            {mqResult && !mqRunning && <MessageQualityCard data={mqResult} />}
          </div>
        )}

        {/* Long-running notice */}
        {running && (
          <div className="mt-4 flex items-start gap-3 rounded-xl border border-sun/40 bg-sun/[0.08] px-4 py-3.5 text-sm text-plum-800/80 dark:text-creamtext/80">
            <span className="mt-0.5 flex-shrink-0">
              <Spinner />
            </span>
            <div>
              <p className="font-semibold text-plum-800 dark:text-creamtext">
                Running the live AI pipeline across all 12 cases…
              </p>
              <p className="mt-0.5 text-[13px] text-plum-800/60 dark:text-creamtext/60">
                This takes several minutes as each case runs through the full
                real pipeline. Please keep this tab open.
              </p>
            </div>
          </div>
        )}

        {/* Run error */}
        {runError && (
          <div className="mt-4 rounded-xl border border-crimson/30 bg-crimson/5 dark:bg-crimson/20 px-4 py-3 text-sm text-crimson">
            {runError}
          </div>
        )}
      </header>

      {/* ── Cases error ── */}
      {casesError && (
        <div className="mb-6 rounded-xl border border-crimson/30 bg-crimson/5 dark:bg-crimson/20 px-4 py-3 text-sm text-crimson">
          {casesError}
        </div>
      )}

      {/* ── Summary banner ── */}
      {hasResults && (
        <div className="mb-8 overflow-hidden rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 shadow-sm">
          <div className="flex flex-wrap items-center gap-6 px-7 py-6">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-plum-800/40 dark:text-creamtext/40">
                Result
              </p>
              <p className={`mt-1 font-serif text-5xl ${matchColor}`}>
                {matchCount}
                <span className="text-plum-800/30 dark:text-creamtext/30">/{total}</span>
              </p>
              <p className="mt-1 text-[13px] text-plum-800/55 dark:text-creamtext/55">
                cases matched expected outcome
              </p>
            </div>
            <div className="h-12 w-px bg-plum-800/[0.1] dark:bg-creamtext/10" />
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-plum-800/40 dark:text-creamtext/40">
                Match rate
              </p>
              <p className={`mt-1 font-serif text-4xl ${matchColor}`}>
                {matchRate}%
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ── Pre-run: cases preview table ── */}
      {!results && (
        <section className="mb-8">
          <h2 className="mb-4 font-serif text-2xl text-plum-800 dark:text-creamtext">
            Test suite
          </h2>

          {cases === null && !casesError && (
            <p className="text-sm text-plum-800/50 dark:text-creamtext/50">Loading cases…</p>
          )}

          {cases && cases.length > 0 && (
            <div className="overflow-hidden rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 shadow-sm">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-plum-800/[0.08] dark:border-creamtext/10 text-left text-[11px] font-semibold uppercase tracking-[0.12em] text-plum-800/40 dark:text-creamtext/40">
                      <th className="px-5 py-4 font-semibold">#</th>
                      <th className="px-4 py-4 font-semibold">Case ID</th>
                      <th className="px-4 py-4 font-semibold">Name</th>
                      <th className="px-4 py-4 font-semibold">
                        Expected Decision
                      </th>
                      <th className="px-4 py-4 font-semibold">Description</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-plum-800/[0.06] dark:divide-creamtext/10">
                    {cases.map((c, idx) => (
                      <tr
                        key={c.case_id}
                        className="transition-colors hover:bg-cream/70 dark:hover:bg-creamtext/5"
                      >
                        <td className="px-5 py-3.5 font-mono text-[12px] text-plum-800/35 dark:text-creamtext/35">
                          {String(idx + 1).padStart(2, "0")}
                        </td>
                        <td className="px-4 py-3.5 font-mono text-[12px] text-plum-800/60 dark:text-creamtext/60">
                          {c.case_id}
                        </td>
                        <td className="px-4 py-3.5 text-sm font-medium text-plum-800 dark:text-creamtext">
                          {c.case_name}
                        </td>
                        <td className="px-4 py-3.5">
                          <StatusPill status={expectedDecision(c)} />
                        </td>
                        <td className="max-w-xs px-4 py-3.5 text-[13px] text-plum-800/55 dark:text-creamtext/55">
                          {c.description ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </section>
      )}

      {/* ── Results table ── */}
      {results && results.length > 0 && (
        <section>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-4">
            <h2 className="font-serif text-2xl text-plum-800 dark:text-creamtext">
              Case results
            </h2>
            <Legend />
          </div>

          <div className="overflow-hidden rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 shadow-sm">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-plum-800/[0.08] dark:border-creamtext/10 text-left text-[11px] font-semibold uppercase tracking-[0.12em] text-plum-800/40 dark:text-creamtext/40">
                    <th className="px-5 py-4 font-semibold">Case ID</th>
                    <th className="px-4 py-4 font-semibold">Name</th>
                    <th className="px-4 py-4 font-semibold">Expected</th>
                    <th className="px-4 py-4 font-semibold">Produced</th>
                    <th className="px-4 py-4 text-right font-semibold">
                      Approved
                    </th>
                    <th className="px-4 py-4 text-center font-semibold">
                      Match
                    </th>
                    <th className="px-4 py-4" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-plum-800/[0.06] dark:divide-creamtext/10">
                  {results.map((row) => (
                    <ResultRow
                      key={row.case_id}
                      row={row}
                      expectedCase={cases?.find(
                        (c) => c.case_id === row.case_id
                      )}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
