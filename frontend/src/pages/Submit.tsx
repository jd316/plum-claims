import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  askPolicy,
  estimatePayout,
  getDocumentRequirements,
  getEvalCases,
  getMembers,
  parseClaim,
  submitClaim,
  submitClaimAsync,
} from "../api";
import type { PayoutEstimate, PolicyAnswer } from "../api";
import type {
  ClaimCategory,
  ClaimResult,
  DocType,
  DocumentProblem,
  DocumentRequirements,
  EvalCase,
  Member,
} from "../types";
import { CATEGORY_LABELS } from "../labels";
import { useAuth } from "../auth-context";
import FileDrop from "../components/FileDrop";
import DocZone from "../components/DocZone";

const CATEGORIES: ClaimCategory[] = [
  "CONSULTATION",
  "DIAGNOSTIC",
  "PHARMACY",
  "DENTAL",
  "VISION",
  "ALTERNATIVE_MEDICINE",
];

const POLICY_ID = "PLUM_GHI_2024";
const TODAY = new Date().toISOString().slice(0, 10);

interface FormState {
  memberId: string;
  category: ClaimCategory | "";
  treatmentDate: string;
  claimedAmount: string;
  hospitalName: string;
  ytdAmount: string;
}

interface FieldErrors {
  memberId?: string;
  category?: string;
  claimedAmount?: string;
  ytdAmount?: string;
  files?: string;
}

const EMPTY_FORM: FormState = {
  memberId: "",
  category: "",
  treatmentDate: TODAY,
  claimedAmount: "",
  hospitalName: "",
  ytdAmount: "",
};

// --- Field shells ------------------------------------------------------------

function Label({
  htmlFor,
  children,
  optional,
}: {
  htmlFor?: string;
  children: React.ReactNode;
  optional?: boolean;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className="mb-1.5 block text-sm font-medium text-plum-800 dark:text-creamtext"
    >
      {children}
      {optional && (
        <span className="ml-1 font-normal text-plum-800/40 dark:text-creamtext/40">(optional)</span>
      )}
    </label>
  );
}

const inputClass =
  "w-full rounded-xl border border-plum-800/15 bg-white px-3.5 py-2.5 text-sm text-plum-800 outline-none transition-colors placeholder:text-plum-800/30 focus:border-coral focus:ring-2 focus:ring-coral/20 dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext dark:placeholder:text-creamtext/30";

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <p className="mt-1 text-xs font-medium text-crimson">{message}</p>;
}

// --- Blocked screen ----------------------------------------------------------

const PROBLEM_TAGS: Record<DocumentProblem["kind"], string> = {
  WRONG_DOCUMENT: "Wrong document",
  MISSING_DOCUMENT: "Missing document",
  UNREADABLE_DOCUMENT: "Unreadable document",
  PATIENT_MISMATCH: "Patient mismatch",
  INTAKE_VIOLATION: "Intake issue",
  NEEDS_MEMBER_INPUT: "More info needed",
};

function BlockedScreen({
  problems,
  onFix,
}: {
  problems: DocumentProblem[];
  onFix: () => void;
}) {
  return (
    <div className="overflow-hidden rounded-card border border-molten/30 bg-white dark:bg-plum-800 shadow-sm">
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
            Action needed before we can process this claim
          </h2>
          <p className="mt-1 text-sm text-plum-800/60 dark:text-creamtext/60">
            We spotted {problems.length === 1 ? "an issue" : "a few issues"} with
            the uploaded documents. Fix the {problems.length === 1 ? "item" : "items"}{" "}
            below and resubmit — nothing has been rejected.
          </p>
        </div>
      </div>

      <ul className="divide-y divide-plum-800/[0.07] px-7">
        {problems.map((problem, i) => (
          <li key={`${problem.kind}-${i}`} className="py-5">
            <span className="inline-block rounded-full bg-molten/10 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-molten">
              {PROBLEM_TAGS[problem.kind] ?? problem.kind}
            </span>
            <p className="mt-2.5 text-[15px] leading-relaxed text-plum-800 dark:text-creamtext">
              {problem.message}
            </p>
          </li>
        ))}
      </ul>

      <div className="border-t border-plum-800/[0.07] dark:border-creamtext/10 px-7 py-5">
        <button
          type="button"
          onClick={onFix}
          className="rounded-full bg-coral px-6 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-plum-800"
        >
          Fix &amp; resubmit
        </button>
      </div>
    </div>
  );
}

// --- Live payout estimate card -----------------------------------------------

const inr = (n: number) =>
  `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;

function EstimateCard({
  estimate,
  loading,
}: {
  estimate: PayoutEstimate | null;
  loading: boolean;
}) {
  if (!estimate && !loading) return null;
  return (
    <div className="rounded-xl border border-plum-800/10 dark:border-creamtext/10 bg-cream px-4 py-3.5 dark:bg-plum-700">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-plum-800 dark:text-creamtext">Estimated payout</p>
        {loading && (
          <svg
            className="h-4 w-4 animate-spin text-plum-800/40 dark:text-creamtext/40"
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
        )}
      </div>
      {estimate && (
        <>
          <p className="mt-1 font-serif text-3xl text-plum-800 dark:text-creamtext">
            ~{inr(estimate.estimated_payout)}
          </p>
          <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-plum-800/60 dark:text-creamtext/60">
            {estimate.network_discount_amount > 0 && (
              <div className="flex gap-1">
                <dt>Network discount:</dt>
                <dd className="font-medium text-plum-800 dark:text-creamtext">
                  −{inr(estimate.network_discount_amount)}
                </dd>
              </div>
            )}
            {estimate.copay_amount > 0 && (
              <div className="flex gap-1">
                <dt>Co-pay:</dt>
                <dd className="font-medium text-plum-800 dark:text-creamtext">
                  −{inr(estimate.copay_amount)}
                </dd>
              </div>
            )}
            {estimate.is_network && (
              <div className="flex gap-1">
                <dt className="text-growthText">In-network hospital</dt>
              </div>
            )}
          </dl>
          <p className="mt-2 text-[11px] leading-relaxed text-plum-800/45 dark:text-creamtext/45">
            {estimate.note}
          </p>
        </>
      )}
    </div>
  );
}

// --- Natural-language claim intake ------------------------------------------
// A "Describe your claim" box that calls /api/claims/parse and pre-fills the
// form. Member still uploads docs + reviews; only empty fields get filled.

function DescribeClaim({
  onFilled,
}: {
  onFilled: (draft: {
    claim_category: ClaimCategory | null;
    claimed_amount: number | null;
    hospital_name: string | null;
    treatment_date: string | null;
  }) => string[];
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filledNote, setFilledNote] = useState<string | null>(null);

  async function handleFill() {
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    setFilledNote(null);
    try {
      const draft = await parseClaim(text.trim());
      const filled = onFilled(draft);
      setFilledNote(
        filled.length > 0
          ? `Filled from your description: ${filled.join(", ")}.`
          : "Nothing new to fill — your form already has those details."
      );
    } catch {
      setError("Couldn't read that just now. Please fill the form manually or try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-8 rounded-xl border border-coral/20 bg-coral/[0.04] px-4 py-4">
      <Label htmlFor="describe-claim">Describe your claim</Label>
      <textarea
        id="describe-claim"
        rows={2}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="e.g. “I saw a doctor at Apollo for a fever, the bill was ₹1,500”"
        className={`${inputClass} resize-y`}
      />
      <div className="mt-2.5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={handleFill}
          disabled={!text.trim() || busy}
          className="inline-flex items-center gap-2 rounded-full bg-coral px-4 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-plum-800 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy && (
            <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4Z" />
            </svg>
          )}
          {busy ? "Reading…" : "Fill form"}
        </button>
        <p className="text-[11px] text-plum-800/50 dark:text-creamtext/50">
          We pre-fill what we can — you still upload documents and review every field.
        </p>
      </div>
      {filledNote && (
        <p className="mt-2 text-xs font-medium text-growthText" aria-live="polite">
          {filledNote}
        </p>
      )}
      {error && (
        <p className="mt-2 text-xs font-medium text-crimson" aria-live="assertive">
          {error}
        </p>
      )}
    </div>
  );
}

// --- Ask the policy (RAG) panel ---------------------------------------------
// A collapsible grounded Q&A panel — answers come only from the policy passages,
// with the cited source titles shown beneath. Read-only; no pipeline.

function AskPolicyPanel() {
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<PolicyAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleAsk() {
    if (!question.trim() || busy) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await askPolicy(question.trim()));
    } catch {
      setError("Couldn't reach the policy assistant. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-6 overflow-hidden rounded-card border border-plum-800/[0.12] bg-white shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-6 py-4 text-left"
      >
        <span className="text-sm font-semibold text-plum-800 dark:text-creamtext">
          Ask the policy
          <span className="ml-2 font-normal text-plum-800/45 dark:text-creamtext/45">
            e.g. “what's covered for dental?”
          </span>
        </span>
        <svg
          className={`h-4 w-4 text-plum-800/50 dark:text-creamtext/50 transition-transform ${open ? "rotate-180" : ""}`}
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open && (
        <div className="border-t border-plum-800/[0.08] dark:border-creamtext/10 px-6 py-5">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              type="text"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAsk();
              }}
              placeholder="Ask anything about your coverage…"
              className={inputClass}
            />
            <button
              type="button"
              onClick={handleAsk}
              disabled={!question.trim() || busy}
              className="inline-flex items-center justify-center gap-2 rounded-full bg-coral px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-plum-800 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? "Asking…" : "Ask"}
            </button>
          </div>
          {error && (
            <p className="mt-3 text-xs font-medium text-crimson" aria-live="assertive">
              {error}
            </p>
          )}
          {result && (
            <div className="mt-4 rounded-xl border border-plum-800/10 dark:border-creamtext/10 bg-cream px-4 py-3.5 dark:bg-plum-700">
              <p className="whitespace-pre-line text-[15px] leading-relaxed text-plum-800 dark:text-creamtext">
                {result.answer}
              </p>
              {result.sources.length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-plum-800/40 dark:text-creamtext/40">
                    Sources
                  </span>
                  {result.sources.map((s) => (
                    <span
                      key={s}
                      className="rounded-full bg-plum-800/[0.06] px-2.5 py-1 text-[11px] font-medium text-plum-800/70 dark:text-creamtext/70"
                    >
                      {s}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --- Wizard progress indicator ----------------------------------------------

const STEPS = [
  { n: 1, label: "Who & what" },
  { n: 2, label: "Treatment details" },
  { n: 3, label: "Documents" },
  { n: 4, label: "Review & submit" },
] as const;

type StepNo = 1 | 2 | 3 | 4;

function StepProgress({
  current,
  maxReached,
  onJump,
}: {
  current: StepNo;
  /** Highest step the user has validly reached — earlier steps are clickable. */
  maxReached: StepNo;
  onJump: (step: StepNo) => void;
}) {
  return (
    <nav aria-label="Progress" className="mb-8">
      <ol className="flex items-center">
        {STEPS.map((s, i) => {
          const done = s.n < current;
          const active = s.n === current;
          const reachable = (s.n as StepNo) <= maxReached;
          return (
            <li
              key={s.n}
              className={`flex items-center ${i < STEPS.length - 1 ? "flex-1" : ""}`}
            >
              <button
                type="button"
                onClick={() => reachable && onJump(s.n as StepNo)}
                disabled={!reachable}
                aria-current={active ? "step" : undefined}
                className={[
                  "group flex items-center gap-2.5 rounded-full py-1 pr-1 text-left transition-colors",
                  reachable ? "cursor-pointer" : "cursor-not-allowed",
                ].join(" ")}
              >
                <span
                  className={[
                    "flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full text-sm font-semibold transition-colors",
                    active
                      ? "bg-coral text-white"
                      : done
                        ? "bg-coral/15 text-coral"
                        : "bg-plum-800/[0.07] text-plum-800/50 dark:bg-creamtext/10 dark:text-creamtext/50",
                  ].join(" ")}
                >
                  {done ? (
                    <svg
                      className="h-4 w-4"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={2.5}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="m5 13 4 4L19 7"
                      />
                    </svg>
                  ) : (
                    s.n
                  )}
                </span>
                <span
                  className={[
                    "hidden whitespace-nowrap text-sm font-medium sm:inline",
                    active
                      ? "text-plum-800 dark:text-creamtext"
                      : "text-plum-800/55 dark:text-creamtext/55",
                  ].join(" ")}
                >
                  {s.label}
                </span>
              </button>
              {i < STEPS.length - 1 && (
                <span
                  aria-hidden
                  className={[
                    "mx-2 h-0.5 flex-1 rounded-full transition-colors",
                    s.n < current
                      ? "bg-coral/40"
                      : "bg-plum-800/10 dark:bg-creamtext/10",
                  ].join(" ")}
                />
              )}
            </li>
          );
        })}
      </ol>
      {/* Compact label for mobile (step labels are hidden inline below sm) */}
      <p className="mt-3 text-sm font-medium text-plum-800 sm:hidden dark:text-creamtext">
        Step {current} of {STEPS.length} —{" "}
        <span className="text-plum-800/60 dark:text-creamtext/60">
          {STEPS[current - 1].label}
        </span>
      </p>
    </nav>
  );
}

// --- Review row helper -------------------------------------------------------

function ReviewRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5">
      <dt className="text-sm text-plum-800/55 dark:text-creamtext/55">{label}</dt>
      <dd className="text-right text-sm font-medium text-plum-800 dark:text-creamtext">
        {value}
      </dd>
    </div>
  );
}

// --- Page --------------------------------------------------------------------

export default function Submit() {
  const navigate = useNavigate();
  const { user } = useAuth();

  // When auth is ON and the principal is a member, their member_id is fixed.
  const fixedMemberId =
    user && user.role === "member" && user.member_id ? user.member_id : null;

  const [members, setMembers] = useState<Member[]>([]);
  const [loadingMembers, setLoadingMembers] = useState(true);
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [form, setForm] = useState<FormState>(
    fixedMemberId ? { ...EMPTY_FORM, memberId: fixedMemberId } : EMPTY_FORM
  );
  // The effective member id: a fixed (auth-member) principal always wins over the
  // form field, so auth that resolves AFTER mount is reflected without an effect
  // that syncs it into form state.
  const effectiveMemberId = fixedMemberId ?? form.memberId;
  // Per-required-slot files keyed by doc type, plus a bag of optional extras.
  const [zoneFiles, setZoneFiles] = useState<Partial<Record<DocType, File>>>({});
  const [extraFiles, setExtraFiles] = useState<File[]>([]);
  const [requirements, setRequirements] = useState<DocumentRequirements | null>(
    null
  );
  const [errors, setErrors] = useState<FieldErrors>({});

  // Wizard navigation. `step` is the visible step; `maxReached` gates which
  // numbered steps can be clicked back to.
  const [step, setStep] = useState<StepNo>(1);
  const [maxReached, setMaxReached] = useState<StepNo>(1);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<DocumentProblem[] | null>(null);
  // OFF by default → the synchronous submitClaim path is byte-for-byte unchanged.
  // ON enqueues via submitClaimAsync and hands the job_id to the Claim page stepper.
  const [processInBackground, setProcessInBackground] = useState(false);

  // Live, informational payout estimate (deterministic; no LLM). Debounced on
  // category/amount/hospital changes. Never blocks submit — purely a preview.
  const [estimate, setEstimate] = useState<PayoutEstimate | null>(null);
  const [estimating, setEstimating] = useState(false);

  // Focus target — the heading of the current step (focus moves here on change).
  const headingRef = useRef<HTMLHeadingElement>(null);

  const isMounted = useRef(true);
  useEffect(() => {
    isMounted.current = true;
    return () => {
      isMounted.current = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    // Settle the three independently: a non-critical endpoint failure (e.g. the
    // sample-cases helper) must not blank the member dropdown and lock the form.
    // Only the members fetch is load-bearing — surface an error solely if it fails.
    // A member submits only for themselves (the Member field is locked to "Your
    // account"), so the ops-only /api/members list is neither needed nor permitted —
    // skipping it avoids a spurious 403 error banner on the member's Submit page.
    Promise.allSettled([
      fixedMemberId ? Promise.resolve<Member[]>([]) : getMembers(),
      // /api/eval/cases is ops-only too; for a member the sample-case loader is hidden
      // (no cases → empty), so skip the fetch to avoid a silent 403.
      fixedMemberId ? Promise.resolve<EvalCase[]>([]) : getEvalCases(),
      getDocumentRequirements(),
    ])
      .then(([mRes, cRes, reqsRes]) => {
        if (!active) return;
        if (mRes.status === "fulfilled") setMembers(mRes.value);
        if (cRes.status === "fulfilled") setCases(cRes.value);
        if (reqsRes.status === "fulfilled") setRequirements(reqsRes.value);
        if (mRes.status === "rejected") {
          const err = mRes.reason;
          setLoadError(
            err instanceof Error ? err.message : "Failed to load form data."
          );
        }
      })
      .finally(() => {
        if (active) setLoadingMembers(false);
      });
    return () => {
      active = false;
    };
    // fixedMemberId is a stable primitive (resolved before Submit mounts), so this
    // still runs once; listed to satisfy exhaustive-deps now that the effect reads it.
  }, [fixedMemberId]);

  // Move focus to the step heading whenever the visible step changes (a11y).
  useEffect(() => {
    if (blocked) return;
    headingRef.current?.focus();
  }, [step, blocked]);

  // Whether the inputs are complete enough to estimate. Derived so the estimate
  // card can simply be hidden when they aren't — no synchronous reset in the effect.
  const estimateAmount = Number(form.claimedAmount);
  const estimateInputsValid =
    !!form.category &&
    !!form.claimedAmount &&
    Number.isFinite(estimateAmount) &&
    estimateAmount > 0;

  // Debounced live estimate: recompute ~400ms after category/amount/hospital
  // settle. Stale responses are ignored via the `cancelled` guard.
  useEffect(() => {
    if (!estimateInputsValid) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      // Spinner shows once typing settles and the fetch actually starts (inside
      // this debounce callback), so there's no synchronous setState in the effect.
      setEstimating(true);
      estimatePayout({
        claim_category: form.category as ClaimCategory,
        claimed_amount: estimateAmount,
        ...(form.hospitalName.trim() ? { hospital_name: form.hospitalName.trim() } : {}),
      })
        .then((e) => {
          if (!cancelled) setEstimate(e);
        })
        .catch(() => {
          // Estimate is purely informational — a failure simply hides the card.
          if (!cancelled) setEstimate(null);
        })
        .finally(() => {
          if (!cancelled) setEstimating(false);
        });
    }, 400);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [estimateInputsValid, estimateAmount, form.category, form.hospitalName]);

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  // Changing the treatment type rebuilds the drop-zones, so clear staged files
  // to avoid carrying a file from one category's slots into another's.
  function changeCategory(value: ClaimCategory | "") {
    setForm((prev) => ({ ...prev, category: value }));
    setZoneFiles({});
    setExtraFiles([]);
    setErrors((prev) => ({ ...prev, files: undefined }));
  }

  const requiredTypes: DocType[] = form.category
    ? requirements?.[form.category]?.required ?? []
    : [];

  // Every required slot must hold a file before submit is enabled. Soft classify
  // warnings never disable submit — the server pipeline stays the source of truth.
  const requiredZonesFilled =
    requiredTypes.length > 0 && requiredTypes.every((t) => zoneFiles[t]);

  function applySampleCase(caseId: string) {
    const sample = cases.find((c) => c.case_id === caseId);
    if (!sample) return;
    const input = sample.input as Record<string, unknown>;
    setForm({
      // When the member is fixed (auth member), keep their own id — a sample
      // case can't reassign the principal.
      memberId: fixedMemberId
        ? fixedMemberId
        : typeof input.member_id === "string"
          ? input.member_id
          : "",
      category:
        typeof input.claim_category === "string"
          ? (input.claim_category as ClaimCategory)
          : "",
      treatmentDate:
        typeof input.treatment_date === "string"
          ? input.treatment_date
          : TODAY,
      claimedAmount:
        typeof input.claimed_amount === "number"
          ? String(input.claimed_amount)
          : "",
      hospitalName:
        typeof input.hospital_name === "string" ? input.hospital_name : "",
      ytdAmount:
        typeof input.ytd_claims_amount === "number"
          ? String(input.ytd_claims_amount)
          : "",
    });
    // Prefill fills the fields only — documents must still be uploaded into the
    // zones (the new category's slots), so clear any staged files.
    setZoneFiles({});
    setExtraFiles([]);
    setErrors({});
  }

  // NL intake: fill ONLY empty form fields from the parsed draft (never override a
  // value the member already set). Returns the human labels of what was filled so
  // the DescribeClaim box can show a subtle "filled from your description" note.
  function prefillFromDraft(draft: {
    claim_category: ClaimCategory | null;
    claimed_amount: number | null;
    hospital_name: string | null;
    treatment_date: string | null;
  }): string[] {
    const filled: string[] = [];
    setForm((prev) => {
      const next = { ...prev };
      if (draft.claim_category && !prev.category) {
        next.category = draft.claim_category;
        filled.push("treatment type");
      }
      if (
        draft.claimed_amount != null &&
        draft.claimed_amount > 0 &&
        !prev.claimedAmount
      ) {
        next.claimedAmount = String(draft.claimed_amount);
        filled.push("amount");
      }
      if (draft.hospital_name && !prev.hospitalName) {
        next.hospitalName = draft.hospital_name;
        filled.push("hospital");
      }
      // Only fill the date when the member hasn't moved it off today's default.
      if (draft.treatment_date && prev.treatmentDate === TODAY) {
        next.treatmentDate = draft.treatment_date;
        filled.push("date");
      }
      return next;
    });
    // Picking a category changes the document drop-zones; clear staged files so a
    // file from another category's slot isn't carried over.
    if (draft.claim_category && !form.category) {
      setZoneFiles({});
      setExtraFiles([]);
    }
    return filled;
  }

  // --- Per-step validation ---------------------------------------------------
  // Each step validates only its own fields; the full validate() runs on submit.

  function validateStep1(): FieldErrors {
    const next: FieldErrors = {};
    if (!effectiveMemberId) next.memberId = "Select a member.";
    if (!form.category) next.category = "Select a treatment type.";
    return next;
  }

  function validateStep2(): FieldErrors {
    const next: FieldErrors = {};
    const amount = Number(form.claimedAmount);
    if (!form.claimedAmount || !Number.isFinite(amount) || amount <= 0) {
      next.claimedAmount = "Enter a claimed amount greater than zero.";
    }
    if (form.ytdAmount.trim() !== "") {
      const ytd = Number(form.ytdAmount);
      if (!Number.isFinite(ytd) || ytd < 0) {
        next.ytdAmount = "YTD claims amount can't be negative.";
      }
    }
    return next;
  }

  function validateStep3(): FieldErrors {
    const next: FieldErrors = {};
    const missing = requiredTypes.filter((t) => !zoneFiles[t]);
    if (missing.length > 0) {
      next.files = "Upload a file into each required document slot.";
    }
    return next;
  }

  function validateForStep(s: StepNo): FieldErrors {
    if (s === 1) return validateStep1();
    if (s === 2) return validateStep2();
    if (s === 3) return validateStep3();
    return {};
  }

  // Is the given step's gate satisfied right now (drives Next button disabled)?
  function stepComplete(s: StepNo): boolean {
    return Object.keys(validateForStep(s)).length === 0;
  }

  // Files in a stable order: each required slot (in policy order), then extras.
  function collectFiles(): File[] {
    const out: File[] = [];
    for (const t of requiredTypes) {
      const f = zoneFiles[t];
      if (f) out.push(f);
    }
    return [...out, ...extraFiles];
  }

  function goNext() {
    const found = validateForStep(step);
    setErrors((prev) => ({ ...prev, ...found }));
    if (Object.keys(found).length > 0) return;
    if (step < 4) {
      const nextStep = (step + 1) as StepNo;
      setErrors({});
      setStep(nextStep);
      setMaxReached((m) => (nextStep > m ? nextStep : m));
    }
  }

  function goBack() {
    if (step > 1) {
      setErrors({});
      setStep((s) => (s - 1) as StepNo);
    }
  }

  // Jump to a previously-reached step (clicking the progress indicator).
  function jumpTo(target: StepNo) {
    if (target > maxReached) return;
    setErrors({});
    setStep(target);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);

    // Validate every step's gate before the network call.
    const all: FieldErrors = {
      ...validateStep1(),
      ...validateStep2(),
      ...validateStep3(),
    };
    setErrors(all);
    if (Object.keys(all).length > 0) {
      // Send the user back to the earliest step with an error.
      if (all.memberId || all.category) setStep(1);
      else if (all.claimedAmount || all.ytdAmount) setStep(2);
      else if (all.files) setStep(3);
      return;
    }

    const payload: Record<string, unknown> = {
      member_id: effectiveMemberId,
      policy_id: POLICY_ID,
      claim_category: form.category,
      treatment_date: form.treatmentDate,
      claimed_amount: Number(form.claimedAmount),
    };
    if (form.hospitalName.trim()) payload.hospital_name = form.hospitalName.trim();
    if (form.ytdAmount.trim() !== "") {
      const ytd = Number(form.ytdAmount);
      if (Number.isFinite(ytd)) payload.ytd_claims_amount = ytd;
    }

    setSubmitting(true);
    try {
      if (processInBackground) {
        // Async path: enqueue and hand the job_id to the Claim page's stepper,
        // which polls getJob() until the decision is ready.
        const ack = await submitClaimAsync(payload, collectFiles());
        if (!isMounted.current) return;
        // Broker-down fallback: the server may run sync and return a completed
        // result inline. Treat that exactly like the sync path below.
        if (ack.status === "completed" && ack.result) {
          const result = ack.result;
          if (result.blocked) {
            setBlocked(result.problems);
            window.scrollTo({ top: 0, behavior: "smooth" });
          } else {
            navigate(`/claims/${result.claim_id}`, { state: { result } });
          }
        } else {
          navigate(`/claims/${ack.claim_id}`, {
            state: { jobId: ack.job_id },
          });
        }
        return;
      }

      const result: ClaimResult = await submitClaim(payload, collectFiles());
      if (!isMounted.current) return;
      if (result.blocked) {
        setBlocked(result.problems);
        window.scrollTo({ top: 0, behavior: "smooth" });
      } else {
        navigate(`/claims/${result.claim_id}`, { state: { result } });
      }
    } catch (err: unknown) {
      if (!isMounted.current) return;
      setSubmitError(
        err instanceof Error ? err.message : "Something went wrong submitting."
      );
    } finally {
      if (isMounted.current) setSubmitting(false);
    }
  }

  function handleFixResubmit() {
    // Keep entered field values; return to the documents step so the member can
    // re-upload (clearing files is acceptable per spec).
    setBlocked(null);
    setZoneFiles({});
    setExtraFiles([]);
    setErrors({});
    setStep(3);
    setMaxReached((m) => (m < 3 ? 3 : m));
  }

  // Enter advances the wizard when the current step is valid (but never from a
  // textarea, and step 4 submits via its own button). Avoids hijacking typing.
  function handleFormKeyDown(e: React.KeyboardEvent<HTMLFormElement>) {
    if (e.key !== "Enter") return;
    const target = e.target as HTMLElement;
    if (target.tagName === "TEXTAREA") return;
    if (step < 4) {
      e.preventDefault();
      goNext();
    }
  }

  const sampleOptions = useMemo(
    () =>
      cases.map((c) => ({
        id: c.case_id,
        label: `${c.case_id} — ${c.case_name}`,
      })),
    [cases]
  );

  const memberName = useMemo(
    () => members.find((m) => m.member_id === effectiveMemberId)?.name ?? null,
    [members, effectiveMemberId]
  );

  const nextDisabled = !stepComplete(step);

  return (
    <div className="mx-auto max-w-2xl px-6 py-12 sm:py-16">
      <header className="mb-8">
        <h1 className="font-serif text-4xl text-plum-800 sm:text-5xl dark:text-creamtext">
          Submit a claim
        </h1>
        <p className="mt-2 max-w-xl text-[15px] leading-relaxed text-plum-800/60 dark:text-creamtext/60">
          Tell us about the treatment and upload the supporting documents. We
          verify everything with a live AI pipeline before processing.
        </p>
      </header>

      {loadError && (
        <div role="alert" className="mb-6 rounded-xl border border-crimson/30 bg-crimson/5 px-4 py-3 text-sm text-crimson">
          {loadError}
        </div>
      )}

      {blocked ? (
        <BlockedScreen problems={blocked} onFix={handleFixResubmit} />
      ) : (
        <>
          <StepProgress current={step} maxReached={maxReached} onJump={jumpTo} />

          <form
            onSubmit={handleSubmit}
            onKeyDown={handleFormKeyDown}
            className="rounded-card border border-plum-800/[0.12] bg-white p-7 shadow-sm sm:p-9 dark:border-creamtext/10 dark:bg-plum-800"
          >
            {/* ===================== STEP 1 — Who & what ===================== */}
            {step === 1 && (
              <section aria-labelledby="step-heading">
                <h2
                  id="step-heading"
                  ref={headingRef}
                  tabIndex={-1}
                  className="font-serif text-2xl text-plum-800 outline-none dark:text-creamtext"
                >
                  Who &amp; what
                </h2>
                <p className="mt-1 mb-6 text-sm text-plum-800/55 dark:text-creamtext/55">
                  Choose the member and the type of treatment being claimed.
                </p>

                {/* Natural-language intake — pre-fills the form */}
                <DescribeClaim onFilled={prefillFromDraft} />

                {/* Sample case loader */}
                {sampleOptions.length > 0 && (
                  <div className="mb-8 rounded-xl border border-plum-800/10 dark:border-creamtext/10 bg-cream px-4 py-3.5 dark:bg-plum-700">
                    <Label htmlFor="sample-case">Load a sample case</Label>
                    <select
                      id="sample-case"
                      aria-label="Load a sample case"
                      defaultValue=""
                      onChange={(e) => {
                        if (e.target.value) applySampleCase(e.target.value);
                      }}
                      className={inputClass}
                    >
                      <option value="">Choose a test case to pre-fill…</option>
                      {sampleOptions.map((opt) => (
                        <option key={opt.id} value={opt.id}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                    <p className="mt-2 text-xs text-plum-800/50 dark:text-creamtext/50">
                      Sample documents must be uploaded manually; render them from
                      the test fixtures.
                    </p>
                  </div>
                )}

                <div className="grid grid-cols-1 gap-5">
                  {/* Member */}
                  <div>
                    <Label htmlFor="member">Member</Label>
                    {fixedMemberId ? (
                      <div className="flex items-center gap-2 rounded-xl border border-plum-800/15 bg-plum-800/[0.03] px-3.5 py-2.5 text-sm text-plum-800 dark:border-creamtext/15 dark:bg-plum-700 dark:text-creamtext">
                        <span className="font-medium">
                          {memberName ?? fixedMemberId}
                        </span>
                        <span className="text-plum-800/45 dark:text-creamtext/45">
                          — {fixedMemberId}
                        </span>
                        <span className="ml-auto rounded-full bg-plum-800/[0.06] px-2 py-0.5 text-[11px] font-medium text-plum-800/60 dark:bg-creamtext/10 dark:text-creamtext/60">
                          Your account
                        </span>
                      </div>
                    ) : (
                      <select
                        id="member"
                        aria-label="Member"
                        value={form.memberId}
                        onChange={(e) => update("memberId", e.target.value)}
                        disabled={loadingMembers}
                        className={`${inputClass} disabled:cursor-not-allowed disabled:opacity-60`}
                      >
                        <option value="">
                          {loadingMembers ? "Loading members…" : "Select a member…"}
                        </option>
                        {members.map((m) => (
                          <option key={m.member_id} value={m.member_id}>
                            {m.name} — {m.member_id}
                          </option>
                        ))}
                      </select>
                    )}
                    <FieldError message={errors.memberId} />
                  </div>

                  {/* Treatment type */}
                  <div>
                    <Label htmlFor="category">Treatment type</Label>
                    <select
                      id="category"
                      aria-label="Treatment type"
                      value={form.category}
                      onChange={(e) =>
                        changeCategory(e.target.value as ClaimCategory | "")
                      }
                      className={inputClass}
                    >
                      <option value="">Select a type…</option>
                      {CATEGORIES.map((c) => (
                        <option key={c} value={c}>
                          {CATEGORY_LABELS[c]}
                        </option>
                      ))}
                    </select>
                    <FieldError message={errors.category} />
                  </div>
                </div>
              </section>
            )}

            {/* ================= STEP 2 — Treatment details ================= */}
            {step === 2 && (
              <section aria-labelledby="step-heading">
                <h2
                  id="step-heading"
                  ref={headingRef}
                  tabIndex={-1}
                  className="font-serif text-2xl text-plum-800 outline-none dark:text-creamtext"
                >
                  Treatment details
                </h2>
                <p className="mt-1 mb-6 text-sm text-plum-800/55 dark:text-creamtext/55">
                  When was the treatment, and how much are you claiming?
                </p>

                <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                  {/* Treatment date */}
                  <div>
                    <Label htmlFor="date">Treatment date</Label>
                    <input
                      id="date"
                      aria-label="Treatment date"
                      type="date"
                      value={form.treatmentDate}
                      onChange={(e) => update("treatmentDate", e.target.value)}
                      className={inputClass}
                    />
                  </div>

                  {/* Claimed amount */}
                  <div>
                    <Label htmlFor="amount">Claimed amount (₹)</Label>
                    <input
                      id="amount"
                      type="number"
                      min="0"
                      step="0.01"
                      inputMode="decimal"
                      placeholder="0.00"
                      value={form.claimedAmount}
                      onChange={(e) => update("claimedAmount", e.target.value)}
                      className={inputClass}
                    />
                    <FieldError message={errors.claimedAmount} />
                  </div>

                  {/* YTD amount */}
                  <div>
                    <Label htmlFor="ytd" optional>
                      YTD claims amount (₹)
                    </Label>
                    <input
                      id="ytd"
                      type="number"
                      min="0"
                      step="0.01"
                      inputMode="decimal"
                      placeholder="0.00"
                      value={form.ytdAmount}
                      onChange={(e) => update("ytdAmount", e.target.value)}
                      className={inputClass}
                    />
                    <FieldError message={errors.ytdAmount} />
                  </div>

                  {/* Hospital */}
                  <div>
                    <Label htmlFor="hospital" optional>
                      Hospital name
                    </Label>
                    <input
                      id="hospital"
                      type="text"
                      placeholder="e.g. City Medical Centre"
                      value={form.hospitalName}
                      onChange={(e) => update("hospitalName", e.target.value)}
                      className={inputClass}
                    />
                  </div>

                  {/* Live payout estimate — informational, never blocks submit.
                      Shown only when the inputs are complete enough to estimate. */}
                  {estimateInputsValid && (
                    <div className="sm:col-span-2">
                      <EstimateCard estimate={estimate} loading={estimating} />
                    </div>
                  )}
                </div>
              </section>
            )}

            {/* ===================== STEP 3 — Documents ===================== */}
            {step === 3 && (
              <section aria-labelledby="step-heading">
                <h2
                  id="step-heading"
                  ref={headingRef}
                  tabIndex={-1}
                  className="font-serif text-2xl text-plum-800 outline-none dark:text-creamtext"
                >
                  Documents
                </h2>
                <p className="mt-1 mb-6 text-sm text-plum-800/55 dark:text-creamtext/55">
                  Upload the documents required for{" "}
                  {form.category ? CATEGORY_LABELS[form.category] : "this claim"}.
                  We check each file the moment you add it.
                </p>

                <div>
                  {!form.category ? (
                    <p className="rounded-xl border border-dashed border-plum-800/15 dark:border-creamtext/10 bg-plum-800/[0.02] px-4 py-6 text-center text-sm text-plum-800/50 dark:text-creamtext/50">
                      Select a treatment type in step 1 to see which documents to
                      upload.
                    </p>
                  ) : (
                    <div className="flex flex-col gap-5">
                      {requiredTypes.map((t) => (
                        <DocZone
                          key={t}
                          expected={t}
                          file={zoneFiles[t] ?? null}
                          onChange={(f) =>
                            setZoneFiles((prev) => {
                              const next = { ...prev };
                              if (f) next[t] = f;
                              else delete next[t];
                              return next;
                            })
                          }
                        />
                      ))}

                      <div className="border-t border-plum-800/[0.07] dark:border-creamtext/10 pt-4">
                        <p className="mb-2 text-sm font-medium text-plum-800 dark:text-creamtext">
                          Additional documents{" "}
                          <span className="font-normal text-plum-800/40 dark:text-creamtext/40">
                            (optional)
                          </span>
                        </p>
                        <FileDrop
                          id="documents"
                          files={extraFiles}
                          onChange={setExtraFiles}
                        />
                      </div>
                    </div>
                  )}
                  <FieldError message={errors.files} />
                </div>
              </section>
            )}

            {/* ================= STEP 4 — Review & submit =================== */}
            {step === 4 && (
              <section aria-labelledby="step-heading">
                <h2
                  id="step-heading"
                  ref={headingRef}
                  tabIndex={-1}
                  className="font-serif text-2xl text-plum-800 outline-none dark:text-creamtext"
                >
                  Review &amp; submit
                </h2>
                <p className="mt-1 mb-6 text-sm text-plum-800/55 dark:text-creamtext/55">
                  Check everything below, then submit for verification.
                </p>

                <dl className="divide-y divide-plum-800/[0.07] dark:divide-creamtext/10">
                  <ReviewRow
                    label="Member"
                    value={
                      memberName
                        ? `${memberName} — ${effectiveMemberId}`
                        : effectiveMemberId || "—"
                    }
                  />
                  <ReviewRow
                    label="Treatment type"
                    value={
                      form.category ? CATEGORY_LABELS[form.category] : "—"
                    }
                  />
                  <ReviewRow label="Treatment date" value={form.treatmentDate} />
                  <ReviewRow
                    label="Claimed amount"
                    value={inr(Number(form.claimedAmount) || 0)}
                  />
                  {form.hospitalName.trim() && (
                    <ReviewRow label="Hospital" value={form.hospitalName} />
                  )}
                  {form.ytdAmount.trim() !== "" && (
                    <ReviewRow
                      label="YTD claims amount"
                      value={inr(Number(form.ytdAmount) || 0)}
                    />
                  )}
                </dl>

                {/* Document list */}
                <div className="mt-6">
                  <p className="mb-2 text-sm font-medium text-plum-800 dark:text-creamtext">
                    Documents
                  </p>
                  <ul className="flex flex-col gap-2">
                    {collectFiles().length === 0 ? (
                      <li className="text-sm text-plum-800/50 dark:text-creamtext/50">
                        No documents attached.
                      </li>
                    ) : (
                      collectFiles().map((f, i) => (
                        <li
                          key={`${f.name}:${f.size}:${i}`}
                          className="flex items-center gap-2 rounded-xl border border-plum-800/10 bg-cream px-3 py-2 text-sm text-plum-800 dark:border-creamtext/10 dark:bg-plum-700 dark:text-creamtext"
                        >
                          <svg
                            className="h-4 w-4 flex-shrink-0 text-plum-800/40 dark:text-creamtext/40"
                            fill="none"
                            viewBox="0 0 24 24"
                            strokeWidth={1.7}
                            stroke="currentColor"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
                            />
                          </svg>
                          <span className="truncate">{f.name}</span>
                        </li>
                      ))
                    )}
                  </ul>
                </div>

                {/* Estimate echo on review */}
                {(estimate || estimating) && (
                  <div className="mt-6">
                    <EstimateCard estimate={estimate} loading={estimating} />
                  </div>
                )}

                {/* Process-in-background toggle — OFF keeps the synchronous path. */}
                <label className="mt-6 flex cursor-pointer items-start gap-3 rounded-xl border border-plum-800/10 bg-cream px-4 py-3.5 dark:border-creamtext/10 dark:bg-plum-700">
                  <input
                    type="checkbox"
                    checked={processInBackground}
                    onChange={(e) => setProcessInBackground(e.target.checked)}
                    disabled={submitting}
                    className="mt-0.5 h-4 w-4 flex-shrink-0 cursor-pointer accent-coral"
                  />
                  <span>
                    <span className="block text-sm font-medium text-plum-800 dark:text-creamtext">
                      Process in background
                    </span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-plum-800/55 dark:text-creamtext/55">
                      Submit instantly and watch the decision arrive on the claim
                      page (Submitted → Processing → Decided), instead of waiting
                      here for the pipeline to finish.
                    </span>
                  </span>
                </label>
              </section>
            )}

            {submitError && (
              <div role="alert" aria-live="assertive" className="mt-6 rounded-xl border border-crimson/30 bg-crimson/5 px-4 py-3 text-sm text-crimson">
                {submitError}
              </div>
            )}

            {/* ===================== Wizard controls ======================= */}
            <div className="mt-8 flex flex-col gap-3 border-t border-plum-800/[0.07] pt-6 sm:flex-row sm:items-center sm:justify-between dark:border-creamtext/10">
              <button
                type="button"
                onClick={goBack}
                disabled={step === 1 || submitting}
                className="inline-flex items-center justify-center gap-2 rounded-full border border-plum-800/15 px-5 py-2.5 text-sm font-semibold text-plum-800 transition-colors hover:border-plum-800/40 disabled:cursor-not-allowed disabled:opacity-40 dark:border-creamtext/15 dark:text-creamtext"
              >
                Back
              </button>

              {step < 4 ? (
                <div className="flex flex-col items-stretch gap-2 sm:items-end">
                  <button
                    type="button"
                    onClick={goNext}
                    disabled={nextDisabled}
                    className="inline-flex items-center justify-center gap-2 rounded-full bg-coral px-7 py-3 text-sm font-semibold text-white transition-colors hover:bg-plum-800 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Next
                  </button>
                </div>
              ) : (
                <div className="flex flex-col items-stretch gap-2 sm:items-end">
                  <button
                    type="submit"
                    disabled={submitting || !requiredZonesFilled}
                    className="inline-flex items-center justify-center gap-2 rounded-full bg-coral px-7 py-3 text-sm font-semibold text-white transition-colors hover:bg-plum-800 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {submitting && (
                      <svg
                        className="h-4 w-4 animate-spin"
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
                    )}
                    {submitting
                      ? processInBackground
                        ? "Submitting…"
                        : "Processing your claim…"
                      : "Submit claim"}
                  </button>
                  <p className="text-xs text-plum-800/45 dark:text-creamtext/45" aria-live="polite">
                    {processInBackground
                      ? "We'll queue this and show progress on the claim page."
                      : submitting
                        ? "Running a live AI verification pipeline — this can take 20–40 seconds."
                        : "Verification runs a live AI pipeline and may take 20–40 seconds."}
                  </p>
                </div>
              )}
            </div>
          </form>
        </>
      )}

      {/* Grounded policy Q&A — collapsible, read-only, no pipeline */}
      <AskPolicyPanel />
    </div>
  );
}
