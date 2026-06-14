import type { FinancialBreakdown } from "../types";
import { formatRupees } from "../utils/format";

export default function FinancialTable({
  financial,
}: {
  financial: FinancialBreakdown;
}) {
  const { line_items, steps } = financial;

  return (
    <section className="overflow-hidden rounded-card border border-plum-800/[0.12] dark:border-creamtext/10 bg-white dark:bg-plum-800 shadow-sm">
      <div className="border-b border-plum-800/[0.08] dark:border-creamtext/10 px-6 py-5 sm:px-8">
        <h2 className="font-serif text-2xl text-plum-800 dark:text-creamtext">Financial breakdown</h2>
        <p className="mt-1 text-sm text-plum-800/55 dark:text-creamtext/55">
          How the approved amount was calculated, line by line.
        </p>
      </div>

      {/* Line items ----------------------------------------------------------- */}
      {line_items.length > 0 && (
        <div className="px-6 py-2 sm:px-8">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] font-semibold uppercase tracking-[0.12em] text-plum-800/40 dark:text-creamtext/40">
                <th className="py-3 font-semibold">Item</th>
                <th className="py-3 text-right font-semibold">Amount</th>
                <th className="py-3 pl-4 text-right font-semibold">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-plum-800/[0.06] dark:divide-creamtext/10">
              {line_items.map((item, i) => (
                <tr key={`${item.description}-${i}`} className="align-top">
                  <td className="py-3.5 pr-4">
                    <p className="font-medium text-plum-800 dark:text-creamtext">
                      {item.description}
                    </p>
                    {!item.approved && item.reason && (
                      <p className="mt-1 text-[13px] leading-relaxed text-crimson/90">
                        {item.reason}
                      </p>
                    )}
                  </td>
                  <td className="whitespace-nowrap py-3.5 text-right font-mono text-plum-800 dark:text-creamtext">
                    {formatRupees(item.amount)}
                  </td>
                  <td className="py-3.5 pl-4 text-right">
                    {item.approved ? (
                      <span
                        className="inline-flex items-center gap-1 rounded-full bg-growth/12 dark:bg-growth/20 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-growthText dark:text-growth"
                        title="Covered under the policy"
                      >
                        <span aria-hidden>✓</span> Approved
                      </span>
                    ) : (
                      <span
                        className="inline-flex cursor-help items-center gap-1 rounded-full bg-crimson/10 dark:bg-crimson/20 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-crimson"
                        title={item.reason || "Not approved under the policy"}
                        aria-label={`Rejected: ${item.reason || "not approved under the policy"}`}
                      >
                        <span aria-hidden>✗</span> Rejected
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Calculation steps ---------------------------------------------------- */}
      <div className="border-t border-plum-800/[0.08] dark:border-creamtext/10 bg-cream/60 dark:bg-plum-900 px-6 py-6 sm:px-8">
        <p className="mb-4 text-[11px] font-semibold uppercase tracking-[0.16em] text-plum-800/40 dark:text-creamtext/40">
          Calculation
        </p>

        {steps.length > 0 ? (
          <ol className="flex flex-col gap-0">
            {steps.map((step, i) => (
              <li
                key={i}
                className="flex items-start gap-3 border-b border-dashed border-plum-800/[0.1] dark:border-creamtext/15 py-2.5 last:border-b-0"
              >
                <span className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full bg-plum-800/[0.06] dark:bg-creamtext/10 text-[11px] font-semibold text-plum-800/60 dark:text-creamtext/60">
                  {i + 1}
                </span>
                <span className="text-sm leading-relaxed text-plum-800/80 dark:text-creamtext/80">
                  {step}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <ol className="flex flex-col gap-2.5 text-sm text-plum-800/80 dark:text-creamtext/80">
            <li className="flex justify-between">
              <span>Gross claimed</span>
              <span className="font-mono">{formatRupees(financial.gross)}</span>
            </li>
            <li className="flex justify-between">
              <span>Network discount ({financial.network_discount_pct}%)</span>
              <span className="font-mono text-crimson">
                −{formatRupees(financial.network_discount_amount)}
              </span>
            </li>
            <li className="flex justify-between border-t border-dashed border-plum-800/15 dark:border-creamtext/15 pt-2.5">
              <span>Post-discount</span>
              <span className="font-mono">
                {formatRupees(financial.post_discount)}
              </span>
            </li>
            <li className="flex justify-between">
              <span>Co-pay ({financial.copay_pct}%)</span>
              <span className="font-mono text-crimson">
                −{formatRupees(financial.copay_amount)}
              </span>
            </li>
          </ol>
        )}

        <div className="mt-5 flex items-baseline justify-between border-t-2 border-plum-800/15 dark:border-creamtext/15 pt-4">
          <span className="font-serif text-lg text-plum-800 dark:text-creamtext">
            Approved amount
          </span>
          <span className="font-serif text-3xl text-growth">
            {formatRupees(financial.approved_amount)}
          </span>
        </div>
      </div>
    </section>
  );
}
