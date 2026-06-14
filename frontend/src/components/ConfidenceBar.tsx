import type { ConfidenceComponents } from "../types";

// Confidence → bar color band.
function bandColor(value: number): { bar: string; text: string } {
  if (value >= 0.85) return { bar: "bg-growth", text: "text-growth" };
  if (value >= 0.6) return { bar: "bg-sun", text: "text-sun" };
  return { bar: "bg-crimson", text: "text-crimson" };
}

const COMPONENT_LABELS: Record<keyof ConfidenceComponents, string> = {
  extraction_quality: "Extraction quality",
  rule_certainty: "Rule certainty",
  completeness: "Completeness",
  verifier_agreement: "Verifier agreement",
  degradation_penalty: "Degradation penalty",
};

const COMPONENT_KEYS: (keyof ConfidenceComponents)[] = [
  "extraction_quality",
  "rule_certainty",
  "completeness",
  "verifier_agreement",
];

function MiniBar({ label, value }: { label: string; value: number }) {
  // Guard against a missing/NaN sub-component from the API so the bar never
  // renders width:NaN% or a "NaN%" label.
  const safe = Number.isFinite(value) ? value : 0;
  const pct = Math.round(Math.max(0, Math.min(1, safe)) * 100);
  const { bar } = bandColor(safe);
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[13px] font-medium text-plum-800/70 dark:text-creamtext/70">{label}</span>
        <span className="font-mono text-[13px] text-plum-800/60 dark:text-creamtext/60">{pct}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-plum-800/[0.08] dark:bg-creamtext/10">
        <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export default function ConfidenceBar({
  confidence,
  components,
}: {
  confidence: number;
  components?: ConfidenceComponents | null;
}) {
  const pct = Math.round(Math.max(0, Math.min(1, confidence)) * 100);
  const { bar, text } = bandColor(confidence);

  return (
    <section className="overflow-hidden rounded-card border border-plum-800/[0.12] bg-white shadow-sm dark:border-creamtext/10 dark:bg-plum-800">
      <div className="px-7 py-6 sm:px-8">
        <div className="flex items-end justify-between">
          <h2 className="font-serif text-2xl text-plum-800 dark:text-creamtext">Confidence</h2>
          <span className={`font-serif text-4xl ${text}`}>{pct}%</span>
        </div>

        <div className="mt-4 h-3 w-full overflow-hidden rounded-full bg-plum-800/[0.08] dark:bg-creamtext/10">
          <div
            className={`h-full rounded-full ${bar} transition-all`}
            style={{ width: `${pct}%` }}
          />
        </div>

        {components && (
          <>
            <div className="mt-7 grid grid-cols-1 gap-x-8 gap-y-5 sm:grid-cols-2">
              {COMPONENT_KEYS.map((key) => (
                <MiniBar
                  key={key}
                  label={COMPONENT_LABELS[key]}
                  value={components[key]}
                />
              ))}
            </div>

            {components.degradation_penalty > 0 && (
              <div className="mt-5 flex items-center gap-2 rounded-xl border border-crimson/25 bg-crimson/5 px-3.5 py-2.5">
                <svg
                  className="h-4 w-4 flex-shrink-0 text-crimson"
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
                <span className="text-[13px] font-medium text-crimson">
                  −{Math.round(components.degradation_penalty * 100)}% degradation
                  penalty applied (a component degraded during processing)
                </span>
              </div>
            )}
          </>
        )}
      </div>
    </section>
  );
}
