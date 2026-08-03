import type { JobStatus, RevocationStatus, RiskTier } from '@/lib/api'
import { cn } from '@/lib/utils'

function Badge({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.08em]',
        className,
      )}
    >
      {children}
    </span>
  )
}

const TIER: Record<RiskTier, { label: string; className: string }> = {
  dangerous: { label: 'Dangerous', className: 'bg-red-500/15 text-red-300' },
  watch: { label: 'Watch', className: 'bg-amber-400/15 text-amber-200' },
  safe: { label: 'Safe', className: 'bg-white/[0.06] text-white/45' },
}

export function TierBadge({ tier }: { tier: RiskTier }) {
  const t = TIER[tier]
  return <Badge className={t.className}>{t.label}</Badge>
}

/**
 * The one piece of rendering the product cannot get wrong (ADR-059/061).
 *
 * Every state except `revoked` means the approval is *still live onchain*, so
 * only `revoked` gets the settled, quiet treatment. `simulated` in particular
 * must never borrow the language or the colour of `revoked`: a dry run that
 * reads as "handled" is exactly the failure that leaves someone drained while
 * their dashboard says they are safe. It is labelled with what is still true.
 */
const REVOCATION: Record<
  RevocationStatus,
  { label: string; className: string; live: boolean }
> = {
  revoked: {
    label: 'Revoked',
    className: 'bg-emerald-400/15 text-emerald-200',
    live: false,
  },
  pending: {
    label: 'Revoking…',
    className: 'bg-sky-400/15 text-sky-200',
    live: true,
  },
  simulated: {
    label: 'Simulated · still live',
    className: 'bg-amber-400/15 text-amber-200',
    live: true,
  },
  failed: {
    label: 'Revoke failed · still live',
    className: 'bg-red-500/15 text-red-300',
    live: true,
  },
  not_attempted: {
    label: 'Not revoked',
    className: 'bg-white/[0.06] text-white/45',
    live: true,
  },
}

export function RevocationBadge({ status }: { status: RevocationStatus }) {
  const s = REVOCATION[status]
  return <Badge className={s.className}>{s.label}</Badge>
}

/** True while the allowance can still be spent by the spender. */
export function isStillLive(status: RevocationStatus): boolean {
  return REVOCATION[status].live
}

const JOB: Record<JobStatus, { label: string; className: string }> = {
  pending: { label: 'Queued', className: 'bg-white/[0.06] text-white/45' },
  running: { label: 'Scanning', className: 'bg-sky-400/15 text-sky-200' },
  awaiting_input: {
    label: 'Needs you',
    className: 'bg-amber-400/15 text-amber-200',
  },
  completed: {
    label: 'Complete',
    className: 'bg-emerald-400/15 text-emerald-200',
  },
  failed: { label: 'Failed', className: 'bg-red-500/15 text-red-300' },
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const s = JOB[status]
  return <Badge className={s.className}>{s.label}</Badge>
}
