'use client'

import type { ApprovalFinding } from '@/lib/api'
import { addressUrl, chainName, truncateAddress, txUrl } from '@/lib/chains'
import { cn } from '@/lib/utils'
import { RevocationBadge, TierBadge, isStillLive } from './status'

/**
 * Findings arrive already sorted most-dangerous-first (ADR-058) — the console
 * does not re-rank them, so what is shown is what the classifier decided.
 */
export function FindingsTable({ findings }: { findings: ApprovalFinding[] }) {
  if (findings.length === 0) {
    return (
      <p className="px-4 py-10 text-center font-mono text-xs text-white/35">
        No outstanding approvals found on the scanned chains.
      </p>
    )
  }

  return (
    <ul data-testid="findings" className="divide-y divide-white/[0.06]">
      {findings.map((f) => (
        <FindingRow key={`${f.chain_id}:${f.token_address}:${f.spender_address}`} f={f} />
      ))}
    </ul>
  )
}

function FindingRow({ f }: { f: ApprovalFinding }) {
  const spender = addressUrl(f.chain_id, f.spender_address)
  const receipt = f.revocation_tx_hash
    ? txUrl(f.chain_id, f.revocation_tx_hash)
    : null
  const unlimited = /^unlimited$/i.test(f.approved_amount)

  return (
    <li
      data-testid="finding"
      data-tier={f.tier}
      data-revocation={f.revocation_status}
      className={cn(
        'flex flex-col gap-3 p-4 md:flex-row md:items-start md:gap-6',
        f.tier === 'dangerous' && 'bg-red-500/[0.03]',
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <TierBadge tier={f.tier} />
          {f.is_new ? (
            <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40">
              new
            </span>
          ) : null}
          <span className="font-mono text-xs text-white/80">
            {f.token_symbol}
          </span>
          <span className="font-mono text-[10px] text-white/30">
            {chainName(f.chain_id)}
          </span>
        </div>

        <p className="font-mono text-xs text-white/55">
          <span className="text-white/35">spender </span>
          {spender ? (
            <a
              href={spender}
              target="_blank"
              rel="noreferrer noopener"
              className="underline decoration-white/20 underline-offset-2 hover:text-white"
            >
              {f.spender_name ?? truncateAddress(f.spender_address)}
            </a>
          ) : (
            (f.spender_name ?? truncateAddress(f.spender_address))
          )}
          {f.spender_name ? (
            <span className="text-white/30">
              {' '}
              · {truncateAddress(f.spender_address)}
            </span>
          ) : null}
        </p>

        {f.explanation ? (
          <p className="max-w-[70ch] text-pretty text-xs leading-relaxed text-white/50">
            {f.explanation}
          </p>
        ) : null}

        {f.malicious_behavior.length > 0 ? (
          <ul className="flex flex-wrap gap-1.5">
            {f.malicious_behavior.map((b) => (
              <li
                key={b}
                className="rounded bg-red-500/10 px-1.5 py-0.5 font-mono text-[10px] text-red-300/80"
              >
                {b.replace(/_/g, ' ')}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="flex shrink-0 flex-col items-start gap-2 md:w-56 md:items-end">
        <span
          className={cn(
            'font-mono text-xs',
            unlimited ? 'text-red-300/90' : 'text-white/60',
          )}
        >
          {f.approved_amount}
          <span className="text-white/30"> allowance</span>
        </span>

        <RevocationBadge status={f.revocation_status} />

        {receipt ? (
          <a
            href={receipt}
            target="_blank"
            rel="noreferrer noopener"
            className="font-mono text-[10px] text-white/40 underline decoration-white/20 underline-offset-2 hover:text-white"
          >
            {truncateAddress(f.revocation_tx_hash!, 10, 8)}
          </a>
        ) : f.revocation_tx_hash ? (
          <span className="font-mono text-[10px] text-white/40">
            {truncateAddress(f.revocation_tx_hash, 10, 8)}
          </span>
        ) : null}

        {f.tier === 'dangerous' && isStillLive(f.revocation_status) ? (
          <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-red-300/80">
            allowance still spendable
          </span>
        ) : null}
      </div>
    </li>
  )
}
