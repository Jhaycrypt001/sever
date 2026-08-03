'use client'

import type { AgentStep } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * The agent's decision journal (ADR-030). Empty in workflow mode, where the
 * pipeline is fixed and there is nothing to explain.
 *
 * `kind` is an open string on the wire on purpose: a newer agent may emit a
 * step kind this console has never seen, and dropping it would hide part of
 * the reasoning. Unknown kinds render with a neutral style.
 */
const KIND_STYLE: Record<string, string> = {
  scan: 'text-sky-200',
  assess: 'text-white/70',
  revoke: 'text-emerald-200',
  ask: 'text-amber-200',
  finish: 'text-white/45',
  // A chain that could not be scanned (ADR-064). Red, not neutral: it is the
  // one step kind that means the run covered less than it was asked to.
  degraded: 'text-red-300',
}

export function Journal({ steps }: { steps: AgentStep[] }) {
  if (steps.length === 0) {
    return (
      <p className="px-4 py-8 text-center font-mono text-xs text-white/35">
        Workflow mode runs a fixed pipeline, so there is no decision journal.
      </p>
    )
  }

  return (
    <ol data-testid="agent-journal" className="divide-y divide-white/[0.06]">
      {[...steps]
        .sort((a, b) => a.seq - b.seq)
        .map((step) => (
          <li key={step.seq} data-kind={step.kind} className="flex gap-4 p-4">
            <span className="w-6 shrink-0 pt-0.5 font-mono text-[10px] text-white/25">
              {String(step.seq).padStart(2, '0')}
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <p className="flex flex-wrap items-baseline gap-2">
                <span
                  className={cn(
                    'font-mono text-[11px] uppercase tracking-[0.08em]',
                    KIND_STYLE[step.kind] ?? 'text-white/60',
                  )}
                >
                  {step.kind}
                </span>
                {step.detail ? (
                  <span className="min-w-0 break-all font-mono text-[11px] text-white/50">
                    {step.detail}
                  </span>
                ) : null}
                {step.new_hits > 0 ? (
                  <span className="font-mono text-[10px] text-white/35">
                    · {step.new_hits} new
                  </span>
                ) : null}
              </p>
              {step.reason ? (
                <p className="text-pretty text-xs leading-relaxed text-white/45">
                  {step.reason}
                </p>
              ) : null}
            </div>
          </li>
        ))}
    </ol>
  )
}
