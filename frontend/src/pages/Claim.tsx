import { useCallback, useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Link, useLocation, useParams } from "react-router-dom";

import { askClaim, getClaim, getClaimAudit, getJob } from "../api";
import type { AuditEntry, JobStatus } from "../api";
import type { ClaimResult, DocumentProblem } from "../types";
import { useAuth } from "../auth-context";
import VerdictCard from "../components/VerdictCard";
import FinancialTable from "../components/FinancialTable";
import ConfidenceBar from "../components/ConfidenceBar";
import TraceTimeline from "../components/TraceTimeline";
import DocumentViewer from "../components/DocumentViewer";
import PipelineMeta from "../components/PipelineMeta";
import Explainability from "../components/Explainability";
import OpsCorrectionPanel from "../components/OpsCorrectionPanel";
import OpsDecisionPanel from "../components/OpsDecisionPanel";

const PROBLEM_TAGS: Record<DocumentProblem["kind"], string> = {
  WRONG_DOCUMENT: "Wrong document",
  MISSING_DOCUMENT: "Missing document",
  UNREADABLE_DOCUMENT: "Unreadable document",
  PATIENT_MISMATCH: "Patient mismatch",
  INTAKE_VIOLATION: "Intake issue",
  NEEDS_MEMBER_INPUT: "More info needed",
};

// Same molten-tag + verbatim-message styling as Submit's blocked screen — a
// stored claim can be a blocked one too.
function BlockedDetail({ problems, showResubmit }: { problems: DocumentProblem[]; showResubmit?: boolean }) {
  return (
    <div className="overflow-hidden rounded-card border border-molten/30 bg-white shadow-sm dark:bg-plum-800">
      <div className="flex items-start gap-4 border-b border-molten/20 bg-molten/[0.06] px-7 py-6">
        <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full bg-molten/15 text-molten">
          <svg
            className="h-6 w-6"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.8}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z"
            />
          </svg>
        </div>
        <div>
          <h2 className="font-serif text-2xl text-plum-800 dark:text-creamtext">
            This claim was blocked before processing
          </h2>
          <p className="mt-1 text-sm text-plum-800/60 dark:text-creamtext/60">
            The uploaded documents had{" "}
            {problems.length === 1 ? "an issue" : "issues"} that prevented a
            decision from being made.
          </p>
        </div>
      </div>

      <ul className="divide-y divide-plum-800/[0.07] px-7 dark:divide-creamtext/10">
        {problems.map((problem, i) => (
          <li key={`${problem.kind}-${i}`} className="py-5">
            <span className="inline-block rounded-full bg-molten/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-molten">
              {PROBLEM_TAGS[problem.kind] ?? problem.kind}
            </span>
            <p className="mt-2.5 break-words text-[15px] leading-relaxed text-plum-800 dark:text-creamtext">
              {problem.message}
            </p>
          </li>
        ))}
      </ul>

      {showResubmit && (
        <div className="border-t border-molten/20 bg-molten/[0.04] px-7 py-5">
          <p className="mb-3 text-sm text-plum-800/60 dark:text-creamtext/60">
            Fix the document{problems.length === 1 ? "" : "s"} noted above, then submit again.
          </p>
          <Link
            to="/"
            className="inline-block rounded-full bg-coral px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-plum-800 dark:hover:bg-plum-700"
          >
            Fix &amp; re-submit
          </Link>
        </div>
      )}
    </div>
  );
}

function CenterState({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto flex max-w-content flex-col items-center justify-center px-6 py-32 text-center">
      {children}
    </div>
  );
}

// --- Async job status stepper ------------------------------------------------
// Lightweight "Submitted → Processing → Decided" tracker. Only rendered when a
// job_id is present (the async submit path). The default UI uses the sync submit,
// so this is purely additive and harmless when no jobId is supplied.

const STEPS = ["Submitted", "Processing", "Decided"] as const;

function stepIndexFor(status: JobStatus["status"] | "completed"): number {
  switch (status) {
    case "queued":
      return 0;
    case "started":
      return 1;
    case "completed":
    case "failed":
      return 2;
    default:
      return 0;
  }
}

function JobStatusStepper({ jobId }: { jobId: string }) {
  const [status, setStatus] = useState<JobStatus["status"]>("queued");

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = () => {
      getJob(jobId)
        .then((j) => {
          if (!active) return;
          setStatus(j.status);
          if (j.status !== "completed" && j.status !== "failed") {
            timer = setTimeout(poll, 2000);
          }
        })
        .catch(() => {
          // A polling hiccup just stops the stepper; the claim still loads.
        });
    };
    poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [jobId]);

  const current = stepIndexFor(status);
  const failed = status === "failed";

  return (
    <div className="mb-6 rounded-card border border-plum-800/[0.12] bg-white px-6 py-4 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
      <div className="flex items-center">
        {STEPS.map((label, i) => {
          const done = i < current || (i === current && status === "completed");
          const active = i === current && status !== "completed";
          return (
            <div key={label} className="flex flex-1 items-center last:flex-none">
              <div className="flex items-center gap-2">
                <span
                  className={[
                    "flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold",
                    failed && i === 2
                      ? "bg-crimson/15 dark:bg-crimson/20 text-crimson"
                      : done
                        ? "bg-growth/20 text-growthText dark:text-growth"
                        : active
                          ? "bg-coral/15 text-coral"
                          : "bg-plum-800/10 text-plum-800/40 dark:bg-creamtext/10 dark:text-creamtext/40",
                  ].join(" ")}
                >
                  {failed && i === 2 ? "!" : done ? "✓" : i + 1}
                </span>
                <span
                  className={[
                    "text-sm font-medium",
                    done || active ? "text-plum-800 dark:text-creamtext" : "text-plum-800/40 dark:text-creamtext/40",
                  ].join(" ")}
                >
                  {failed && i === 2 ? "Failed" : label}
                </span>
              </div>
              {i < STEPS.length - 1 && (
                <span
                  className={[
                    "mx-3 h-px flex-1",
                    i < current ? "bg-growth/40" : "bg-plum-800/10 dark:bg-creamtext/10",
                  ].join(" ")}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// --- Per-claim chat assistant ------------------------------------------------
// A collapsible "Ask about this claim" panel. Read-only — answers come grounded
// only in this claim's stored data; it never changes any decision.

interface ChatTurn {
  role: "user" | "assistant";
  text: string;
}

const SUGGESTED_QUESTIONS = [
  "Why was this decided this way?",
  "How was my payout calculated?",
  "What can I do next?",
];

function ClaimChat({ claimId }: { claimId: string }) {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns, busy]);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || busy) return;
    setError(null);
    setInput("");
    setTurns((prev) => [...prev, { role: "user", text: q }]);
    setBusy(true);
    try {
      const { answer } = await askClaim(claimId, q);
      setTurns((prev) => [...prev, { role: "assistant", text: answer }]);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Couldn't get an answer. Please retry."
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="overflow-hidden rounded-card border border-plum-800/[0.12] bg-white shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-6 py-4 text-left sm:px-8"
      >
        <span className="flex items-center gap-2.5">
          <svg
            className="h-5 w-5 text-coral"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={1.7}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z"
            />
          </svg>
          <span className="font-serif text-lg text-plum-800 dark:text-creamtext">
            Ask about this claim
          </span>
        </span>
        <svg
          className={`h-5 w-5 text-plum-800/40 transition-transform dark:text-creamtext/40 ${
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
      </button>

      {open && (
        <div className="border-t border-plum-800/[0.07] px-6 py-5 dark:border-creamtext/10 sm:px-8">
          {turns.length > 0 && (
            <div
              ref={scrollRef}
              className="mb-4 flex max-h-72 flex-col gap-3 overflow-y-auto"
            >
              {turns.map((t, i) => (
                <div
                  key={i}
                  className={
                    t.role === "user" ? "flex justify-end" : "flex justify-start"
                  }
                >
                  <p
                    className={[
                      "max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm leading-relaxed",
                      t.role === "user"
                        ? "bg-coral text-white"
                        : "bg-cream text-plum-800 dark:bg-plum-900 dark:text-creamtext",
                    ].join(" ")}
                  >
                    {t.text}
                  </p>
                </div>
              ))}
              {busy && (
                <div className="flex justify-start">
                  <p className="rounded-2xl bg-cream px-3.5 py-2 text-sm text-plum-800/50 dark:bg-plum-900 dark:text-creamtext/50">
                    Thinking…
                  </p>
                </div>
              )}
            </div>
          )}

          {turns.length === 0 && (
            <div className="mb-4 flex flex-wrap gap-2">
              {SUGGESTED_QUESTIONS.map((q) => (
                <button
                  key={q}
                  type="button"
                  onClick={() => ask(q)}
                  disabled={busy}
                  className="rounded-full border border-plum-800/15 bg-white px-3 py-1.5 text-xs font-medium text-plum-800 transition-colors hover:border-coral/60 hover:text-coral disabled:opacity-50 dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext"
                >
                  {q}
                </button>
              ))}
            </div>
          )}

          {error && (
            <p className="mb-3 text-xs font-medium text-crimson">{error}</p>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              ask(input);
            }}
            className="flex items-center gap-2"
          >
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask a question about this claim…"
              aria-label="Ask a question about this claim"
              className="w-full rounded-xl border border-plum-800/15 bg-white px-3.5 py-2.5 text-sm text-plum-800 outline-none transition-colors placeholder:text-plum-800/30 focus:border-coral focus:ring-2 focus:ring-coral/20 dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext dark:placeholder:text-creamtext/30"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="flex-shrink-0 rounded-full bg-coral px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-plum-800 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Ask
            </button>
          </form>
          <p className="mt-2 text-[11px] text-plum-800/40 dark:text-creamtext/40">
            Answers are based only on this claim's details. This assistant can't
            change any decision.
          </p>
        </div>
      )}
    </div>
  );
}

// --- Append-only audit trail (ops) -------------------------------------------
// The decision & correction history for a claim: actor, action, the resulting
// decision (status + approved amount), and a timestamp — oldest first. Read-only.
// Gated to ops when auth is ON; open when auth is OFF. Tolerant of an empty list.

const inrAudit = (n: number | null) =>
  n == null ? "—" : `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

const ACTION_LABELS: Record<string, string> = {
  DECISION: "Decision",
  CORRECTION: "Ops correction",
};

// A DECISION row stores reason_codes as a list of code strings; a CORRECTION row
// stores a { changed_fields, before, after } object. Normalize to display chips:
// codes for a decision, changed field names for a correction.
function auditChips(reasonCodes: AuditEntry["reason_codes"]): string[] {
  if (Array.isArray(reasonCodes)) {
    return reasonCodes.map((c) => String(c)).filter(Boolean);
  }
  if (reasonCodes && typeof reasonCodes === "object") {
    const cf = (reasonCodes as { changed_fields?: unknown }).changed_fields;
    if (Array.isArray(cf)) return cf.map((c) => String(c)).filter(Boolean);
  }
  return [];
}

function ClaimAudit({ claimId, reloadKey }: { claimId: string; reloadKey: number }) {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getClaimAudit(claimId)
      .then((rows) => {
        if (active) setEntries(rows);
      })
      .catch((err: unknown) => {
        if (active)
          setError(err instanceof Error ? err.message : "Couldn't load history.");
      });
    return () => {
      active = false;
    };
  }, [claimId, reloadKey]);

  // While loading, on error, or with no rows we render nothing intrusive — the
  // audit trail is supplementary context, never a blocker.
  if (error || entries === null) return null;

  return (
    <section className="rounded-2xl border border-plum-800/10 bg-white/70 p-5 shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
      <h3 className="mb-1 font-serif text-lg text-plum-800 dark:text-creamtext">
        Decision &amp; correction history
      </h3>
      <p className="mb-4 text-xs text-plum-800/55 dark:text-creamtext/55">
        Append-only audit trail — oldest first.
      </p>
      {entries.length === 0 ? (
        <p className="text-sm text-plum-800/45 dark:text-creamtext/45">No corrections recorded.</p>
      ) : (
        <ol className="space-y-2">
          {entries.map((e) => {
            const chips = auditChips(e.reason_codes);
            return (
              <li
                key={String(e.id)}
                className="rounded-lg border border-plum-800/10 px-3 py-2 text-xs text-plum-800/65 dark:border-creamtext/10 dark:text-creamtext/65"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded bg-plum-800/[0.06] px-1.5 py-0.5 font-semibold uppercase tracking-wide text-plum-800/55 dark:bg-creamtext/10 dark:text-creamtext/55">
                    {ACTION_LABELS[e.action] ?? e.action}
                  </span>
                  {e.actor && (
                    <span className="font-semibold text-plum-800/75 dark:text-creamtext/75">
                      {e.actor}
                    </span>
                  )}
                  {e.decision_status && (
                    <span className="font-semibold text-plum-800/75 dark:text-creamtext/75">
                      {e.decision_status}
                    </span>
                  )}
                  <span className="font-mono">{inrAudit(e.approved_amount)}</span>
                  {e.created_at && (
                    <span className="ml-auto text-plum-800/40 dark:text-creamtext/40">
                      {new Date(e.created_at).toLocaleString()}
                    </span>
                  )}
                </div>
                {chips.length > 0 && (
                  <div className="mt-1 flex flex-wrap items-center gap-1 text-plum-800/50 dark:text-creamtext/50">
                    <span className="text-[10px] uppercase tracking-wide text-plum-800/35 dark:text-creamtext/35">
                      {e.action === "CORRECTION" ? "fields" : "codes"}
                    </span>
                    {chips.map((c, i) => (
                      <span
                        key={`${c}-${i}`}
                        className="rounded bg-plum-800/[0.05] px-1.5 py-0.5 font-mono text-[10px] dark:bg-creamtext/10"
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

// --- Tabbed decision panel ---------------------------------------------------
// An accessible tab group (role=tablist/tab/tabpanel) for the decision-review
// right pane. Arrow keys move between tabs, Home/End jump to ends, and the
// active tab carries the coral accent. The verdict + pipeline strip live ABOVE
// this group (pinned), so the at-a-glance conclusion is never hidden.

interface TabDef {
  id: string;
  label: string;
  content: React.ReactNode;
}

function DecisionTabs({ tabs, defaultTabId }: { tabs: TabDef[]; defaultTabId?: string }) {
  const initial = tabs.some((t) => t.id === defaultTabId) ? defaultTabId! : (tabs[0]?.id ?? "");
  const [activeRaw, setActive] = useState(initial);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  // Derived during render: if the set of tabs changes (e.g. the Ops tab appears)
  // and the selected id is gone, fall back to the first tab so we never point at a
  // missing panel — no effect + setState needed.
  const active = tabs.some((t) => t.id === activeRaw)
    ? activeRaw
    : (tabs[0]?.id ?? "");

  const focusTab = (id: string) => {
    setActive(id);
    tabRefs.current[id]?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const idx = tabs.findIndex((t) => t.id === active);
    if (idx < 0) return;
    let next: number;
    switch (e.key) {
      case "ArrowRight":
      case "ArrowDown":
        next = (idx + 1) % tabs.length;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        next = (idx - 1 + tabs.length) % tabs.length;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = tabs.length - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    focusTab(tabs[next].id);
  };

  const activeTab = tabs.find((t) => t.id === active) ?? tabs[0];

  return (
    <div>
      <div
        role="tablist"
        aria-label="Decision details"
        aria-orientation="horizontal"
        className="flex gap-1 overflow-x-auto rounded-full border border-plum-800/[0.1] bg-white p-1 shadow-sm dark:border-creamtext/10 dark:bg-plum-700"
      >
        {tabs.map((t) => {
          const selected = t.id === activeTab?.id;
          return (
            <button
              key={t.id}
              ref={(el) => {
                tabRefs.current[t.id] = el;
              }}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={selected}
              aria-controls={`tabpanel-${t.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActive(t.id)}
              onKeyDown={onKeyDown}
              className={[
                "flex-shrink-0 whitespace-nowrap rounded-full px-4 py-2 text-sm font-semibold transition-colors",
                selected
                  ? "bg-coral text-white shadow-sm"
                  : "text-plum-800/65 hover:bg-plum-800/5 dark:text-creamtext/65 dark:hover:bg-creamtext/5",
              ].join(" ")}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {activeTab && (
        <div
          role="tabpanel"
          id={`tabpanel-${activeTab.id}`}
          aria-labelledby={`tab-${activeTab.id}`}
          tabIndex={0}
          className="mt-5 flex flex-col gap-6 focus:outline-none"
        >
          {activeTab.content}
        </div>
      )}
    </div>
  );
}

export default function Claim() {
  const { id } = useParams<{ id: string }>();
  const { enabled, user } = useAuth();
  const isOps = !enabled || user?.role === "ops";
  const location = useLocation();
  const navState = location.state as
    | { result?: ClaimResult; jobId?: string }
    | null;
  const stateResult = navState?.result;
  const jobId = navState?.jobId;

  const [result, setResult] = useState<ClaimResult | null>(
    stateResult && stateResult.claim_id === id ? stateResult : null
  );
  const [loading, setLoading] = useState(!result);
  const [error, setError] = useState<string | null>(null);
  // null = unknown (still fetching); true/false once the viewer reports back.
  const [hasDocs, setHasDocs] = useState<boolean | null>(null);
  const handleDocAvailability = useCallback((v: boolean) => setHasDocs(v), []);
  // Bumped after an ops correction so the audit trail re-fetches its new row.
  const [auditReloadKey, setAuditReloadKey] = useState(0);

  // After an ops correction the stored claim changes (new decision + history); re-fetch
  // so the verdict, financial table, and history reflect the corrected state.
  const reloadClaim = useCallback(() => {
    setAuditReloadKey((k) => k + 1);
    if (!id) return;
    getClaim(id)
      .then((r) => setResult(r))
      .catch(() => {
        /* keep current view; the inline before/after already showed the result */
      });
  }, [id]);

  useEffect(() => {
    if (result || !id) return;
    let active = true;
    // `loading` is initialized to `!result`, so it is already true whenever this
    // fetch runs (the guard above only proceeds when there's no result yet) — no
    // synchronous setState needed; the finally below clears it.
    getClaim(id)
      .then((r) => {
        if (active) setResult(r);
      })
      .catch((err: unknown) => {
        if (active)
          setError(err instanceof Error ? err.message : "Failed to load claim.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id, result]);

  if (loading) {
    return (
      <CenterState>
        <svg
          className="h-8 w-8 animate-spin text-coral"
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
        <p className="mt-4 text-sm text-plum-800/55 dark:text-creamtext/55">Loading claim…</p>
      </CenterState>
    );
  }

  if (error || !result) {
    return (
      <CenterState>
        <h1 className="font-serif text-4xl text-plum-800 dark:text-creamtext">Claim not found</h1>
        <p className="mt-3 max-w-md text-sm text-plum-800/60 dark:text-creamtext/60">
          {error ?? `We couldn't find a claim with id ${id}.`}
        </p>
        <Link
          to="/claims"
          className="mt-6 rounded-full bg-coral px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-plum-800"
        >
          Back to claims
        </Link>
      </CenterState>
    );
  }

  const { decision } = result;
  const split = hasDocs === true;

  // The machine's conclusion + trace (the right pane in the split layout).
  const conclusion = result.blocked ? (
    <BlockedDetail problems={result.problems} showResubmit={!isOps} />
  ) : decision ? (
    (() => {
      // Pinned, above the tabs: the at-a-glance verdict + the pipeline strip.
      // Everything else is grouped into accessible tabs so nothing is dropped.
      const hasExtractions = (result.extractions?.length ?? 0) > 0;
      // The Ops tab is only relevant when ops tooling would actually render
      // something: the correction panel (ops + extractions) or the audit trail.
      const showOps = (isOps && hasExtractions) || Boolean(id && isOps);

      const tabs: TabDef[] = [
        {
          id: "breakdown",
          label: "Breakdown",
          content: (
            <>
              {decision.financial && (
                <FinancialTable financial={decision.financial} />
              )}
              <ConfidenceBar
                confidence={decision.confidence}
                components={decision.confidence_components}
              />
            </>
          ),
        },
        {
          id: "trace",
          label: "Trace",
          content:
            result.trace && result.trace.length > 0 ? (
              <TraceTimeline trace={result.trace} />
            ) : (
              <p className="text-sm text-plum-800/50 dark:text-creamtext/50">
                No trace steps recorded for this claim.
              </p>
            ),
        },
        {
          id: "explain",
          label: "Explain",
          content: (
            <>
              {id && decision.status !== "APPROVED" && (
                <Explainability claimId={id} status={decision.status} />
              )}
              {id && <ClaimChat claimId={id} />}
            </>
          ),
        },
        ...(showOps
          ? [
              {
                id: "ops",
                label: "Ops",
                content: (
                  <>
                    <OpsDecisionPanel claim={result} onDecided={reloadClaim} />
                    {hasExtractions && (
                      <OpsCorrectionPanel
                        claim={result}
                        onCorrected={reloadClaim}
                      />
                    )}
                    {id && isOps && (
                      <ClaimAudit claimId={id} reloadKey={auditReloadKey} />
                    )}
                  </>
                ),
              } as TabDef,
            ]
          : []),
      ];

      return (
        <div className="flex flex-col gap-6">
          <PipelineMeta result={result} />
          <VerdictCard decision={decision} />
          <DecisionTabs
            tabs={tabs}
            defaultTabId={
              isOps && decision?.status === "MANUAL_REVIEW" ? "ops" : undefined
            }
          />
        </div>
      );
    })()
  ) : (
    <CenterState>
      <h1 className="font-serif text-3xl text-plum-800 dark:text-creamtext">No decision recorded</h1>
      <p className="mt-3 text-sm text-plum-800/60 dark:text-creamtext/60">
        This claim has no decision and was not blocked.
      </p>
    </CenterState>
  );

  // The viewer is always mounted so it can report doc availability; when there
  // are no documents it renders null and we keep the original single column.
  const viewer = id ? (
    <DocumentViewer claimId={id} onAvailabilityChange={handleDocAvailability} />
  ) : null;

  return (
    <div
      className={`mx-auto px-6 py-10 sm:py-14 ${
        split ? "max-w-content" : "max-w-3xl"
      }`}
    >
      <div className="mb-6 flex items-center justify-between">
        <Link
          to="/claims"
          className="inline-flex items-center gap-1.5 text-sm font-medium text-plum-800/55 transition-colors hover:text-coral dark:text-creamtext/55"
        >
          <svg
            className="h-4 w-4"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={2}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M15.75 19.5 8.25 12l7.5-7.5"
            />
          </svg>
          All claims
        </Link>
        <span className="ml-3 truncate font-mono text-xs text-plum-800/40 dark:text-creamtext/40">
          {result.claim_id}
        </span>
      </div>

      {jobId && <JobStatusStepper jobId={jobId} />}

      {split ? (
        <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:items-start lg:gap-6">
          <div className="mb-6 lg:sticky lg:top-6 lg:mb-0">{viewer}</div>
          <div>{conclusion}</div>
        </div>
      ) : (
        <>
          {viewer}
          {conclusion}
        </>
      )}
    </div>
  );
}
