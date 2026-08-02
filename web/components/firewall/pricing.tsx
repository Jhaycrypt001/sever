import { cn } from '@/lib/utils'
import { Display, Label, Shell } from './primitives'

type Ticket = {
  tier: string
  code: string
  blurb: string
  includes: string[]
  lines: { k: string; v: string }[]
  total: string
  period?: string
  auth: string
  cta: string
  featured?: boolean
}

const TICKETS: Ticket[] = [
  {
    tier: 'Watchtower',
    code: 'AFW-WT-2026',
    blurb: 'Read-only. Scan any address, revoke nothing.',
    includes: [
      '1 wallet · 5 chains',
      'Full approval inventory',
      'Safe · Watch · Dangerous tiers',
      'Manual revoke links',
      'No signing authority granted',
    ],
    lines: [
      { k: 'Subtotal', v: '$0.00' },
      { k: 'Gas', v: 'not applicable' },
    ],
    total: 'Free',
    auth: 'afwwt2026',
    cta: 'Scan a wallet',
  },
  {
    tier: 'Firewall',
    code: 'AFW-FW-2026',
    blurb: 'The agent revokes for you, on a schedule.',
    includes: [
      '10 wallets · 5 chains',
      'Everything in Watchtower',
      'Automatic revocation of Dangerous',
      'Recurring scans · new-approval alerts',
      'Digest webhooks · signed',
      'Gas sponsored by KeeperHub',
      'Full audit trail with tx hashes',
    ],
    lines: [
      { k: 'Subtotal', v: '$29.00' },
      { k: 'Per-revocation fee', v: '$0.00' },
      { k: 'Gas passed on', v: '$0.00' },
      { k: 'Hidden charges', v: '$0.00' },
    ],
    total: '$29',
    period: '/ mo',
    auth: 'afwfw2026',
    cta: 'Protect a wallet',
    featured: true,
  },
  {
    tier: 'Treasury',
    code: 'AFW-TR-2026',
    blurb: 'For the multisig that cannot afford a bad day.',
    includes: [
      'Unlimited wallets',
      'Everything in Firewall',
      'Custom risk policy',
      'Self-hosted deployment',
      'Private threat-intel sources',
      'Incident support',
    ],
    lines: [
      { k: 'Subtotal', v: 'scaled to you' },
      { k: 'Setup fee', v: '$0.00' },
    ],
    total: 'Custom',
    auth: 'afwtr2026',
    cta: 'Talk to us',
  },
]

function TicketCard({ t }: { t: Ticket }) {
  const inv = t.featured

  return (
    <li
      className={cn(
        'relative flex flex-col border border-border/60',
        inv ? 'bg-foreground text-background' : 'bg-card/40 text-foreground',
      )}
    >
      {inv ? (
        <p className="label border-b border-background/15 py-2.5 text-center text-background/80">
          ★ Most protected ★
        </p>
      ) : null}

      <div className="flex flex-col gap-2 border-b border-dashed border-current/20 p-6">
        <p className="display text-2xl">
          <span className={inv ? 'text-background/50' : 'text-muted-foreground'}>
            Firewall ·{' '}
          </span>
          {t.tier}
        </p>
        <Label className={inv ? 'text-background/60' : undefined}>
          № {t.code}
        </Label>
        <p
          className={cn(
            'mt-2 text-pretty text-sm leading-relaxed',
            inv ? 'text-background/70' : 'text-muted-foreground',
          )}
        >
          {t.blurb}
        </p>
      </div>

      <div className="flex flex-1 flex-col p-6">
        <Label className={inv ? 'text-background/60' : undefined}>
          Includes
        </Label>

        <ul className="mt-4 flex flex-col gap-3">
          {t.includes.map((f) => (
            <li key={f} className="flex items-baseline gap-3 text-sm">
              <span
                aria-hidden="true"
                className={cn(
                  'font-mono text-xs',
                  inv ? 'text-background/40' : 'text-muted-foreground',
                )}
              >
                ·
              </span>
              <span className="min-w-0 flex-1 text-pretty">{f}</span>
              <span
                className={cn(
                  'label shrink-0',
                  inv ? 'text-background/40' : 'text-muted-foreground',
                )}
              >
                incl.
              </span>
            </li>
          ))}
        </ul>

        <dl className="mt-8 flex flex-col gap-2 border-t border-dashed border-current/20 pt-6">
          {t.lines.map((l) => (
            <div key={l.k} className="flex items-baseline justify-between gap-4">
              <dt
                className={cn(
                  'font-mono text-xs',
                  inv ? 'text-background/60' : 'text-muted-foreground',
                )}
              >
                {l.k}
              </dt>
              <dd className="font-mono text-xs tabular-nums">{l.v}</dd>
            </div>
          ))}
        </dl>

        <div className="mt-6 flex items-baseline justify-between gap-4 border-t border-dashed border-current/20 pt-6">
          <Label className={inv ? 'text-background/60' : undefined}>
            Total
          </Label>
          <p className="display text-3xl">
            {t.total}
            {t.period ? (
              <span
                className={cn(
                  'ml-1 font-mono text-xs font-normal',
                  inv ? 'text-background/50' : 'text-muted-foreground',
                )}
              >
                {t.period}
              </span>
            ) : null}
          </p>
        </div>

        {/* stub */}
        <div className="mt-8 flex items-center justify-between gap-4 border-t border-dashed border-current/20 pt-6">
          <div className="flex flex-col gap-1.5">
            <Label className={inv ? 'text-background/50' : undefined}>
              Auth · 0x
            </Label>
            <span className="font-mono text-xs tracking-[0.14em]">{t.auth}</span>
          </div>
          <Label className={inv ? 'text-background/50' : undefined}>
            Holder · your wallet
          </Label>
        </div>

        <a
          href="#scan"
          className={cn(
            'mt-8 inline-flex items-center justify-center rounded-full px-6 py-3.5 text-[0.9375rem] font-medium tracking-tight transition-opacity hover:opacity-85',
            inv
              ? 'bg-background text-foreground'
              : 'bg-foreground text-background',
          )}
        >
          {t.cta}
        </a>
      </div>
    </li>
  )
}

export function Pricing() {
  return (
    <section
      id="pricing"
      className="border-t border-border/60 bg-background py-20 md:py-28"
    >
      <Shell>
        <Label>§ Ledger · three tickets · 2026.08</Label>

        <Display
          lead="Scanning is free."
          trail="You pay for the revoking."
          className="mt-8 max-w-[24ch] text-[2.5rem] sm:text-[3.25rem] md:text-[4rem]"
        />

        <ul className="mt-16 grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
          {TICKETS.map((t) => (
            <TicketCard key={t.tier} t={t} />
          ))}
        </ul>

        <p className="mt-10 text-center">
          <Label>
            Gas is sponsored on every plan · no per-revocation fee · cancel any
            time
          </Label>
        </p>
      </Shell>
    </section>
  )
}
