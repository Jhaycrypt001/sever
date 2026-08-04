import { CHAIN_COUNT, SUPPORTED_CHAINS } from '@/lib/proof'
import { Reveal, RevealGroup, RevealItem } from '../motion/reveal'
import { Display, Label, Shell } from './primitives'

/**
 * What a run actually costs.
 *
 * This section used to advertise three subscription tiers — `$29 / mo`,
 * product codes, a "Most protected" badge, "cancel any time", and features
 * like self-hosted deployment, private threat-intel sources and incident
 * support. None of it existed. There is no billing system in this codebase,
 * no per-wallet limit is enforced anywhere, and the plans claimed five chains
 * while the scanner reads three.
 *
 * A product whose whole argument is "we only act on verified signals" cannot
 * open with an invented price list. So the numbers here are measured ones, and
 * where there is nothing to charge, it says so.
 */

type Line = { k: string; v: string; note?: string }

const MEASURED: Line[] = [
  {
    k: 'Threat intelligence',
    v: '$0.00',
    note: 'GoPlus, keyless tier',
  },
  {
    k: 'Model calls per scan',
    v: '$0.016',
    note: 'measured on a real three-chain run, not estimated',
  },
  {
    k: 'Gas per revocation',
    v: '$0.00',
    note: 'relayed by KeeperHub — the wallet held 0 ETH and still executed',
  },
  {
    k: 'Charged to you',
    v: 'nothing',
    note: 'there is no billing system in this build',
  },
]

const LIMITS: Line[] = [
  {
    k: 'Chains scanned',
    v: String(CHAIN_COUNT),
    note: SUPPORTED_CHAINS.map((c) => c.name).join(' · '),
  },
  {
    k: 'Wallets it can scan',
    v: 'any',
    note: 'reading approvals is public and needs nobody’s permission',
  },
  {
    k: 'Wallets it can revoke for',
    v: 'one',
    note: 'the wallet delegated to KeeperHub, and only that one',
  },
  {
    k: 'Accounts',
    v: 'open',
    note: 'an email and a code — no invite, no waitlist',
  },
]

function Ledger({ title, lines }: { title: string; lines: Line[] }) {
  return (
    <div className="h-full rounded-lg border border-border/60 bg-card/40 p-6 md:p-8">
      <Label>{title}</Label>
      <dl className="mt-6 flex flex-col gap-5">
        {lines.map((l) => (
          <div key={l.k} className="flex flex-col gap-1">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4">
              <dt className="text-sm text-foreground/75">{l.k}</dt>
              <dd className="font-mono text-sm text-foreground">{l.v}</dd>
            </div>
            {l.note ? (
              <p className="text-pretty text-xs leading-relaxed text-muted-foreground">
                {l.note}
              </p>
            ) : null}
          </div>
        ))}
      </dl>
    </div>
  )
}

export function Pricing() {
  return (
    <section
      id="pricing"
      className="border-t border-border/60 bg-background py-20 md:py-28"
    >
      <Shell>
        <Label>§ Cost · measured, not projected</Label>

        <Reveal>
          <Display
            lead="Nothing is for sale."
            trail="Here is what a run costs to produce."
            className="mt-8 max-w-[26ch] text-[2.5rem] sm:text-[3.25rem] md:text-[4rem]"
          />
        </Reveal>

        <Reveal>
          <p className="mt-8 max-w-[62ch] text-pretty text-base leading-relaxed text-muted-foreground">
            This is a hackathon build. There is no billing, no subscription and
            no plan to upgrade to, so the honest version of a pricing page is
            the cost sheet and the limits. Both are below, and both are read
            off the running system rather than forecast.
          </p>
        </Reveal>

        <RevealGroup
          className="mt-14 grid grid-cols-1 items-start gap-6 lg:grid-cols-2"
          stagger={0.12}
        >
          <RevealItem className="h-full">
            <Ledger title="What one scan costs to run" lines={MEASURED} />
          </RevealItem>
          <RevealItem className="h-full">
            <Ledger title="What it covers, and what it does not" lines={LIMITS} />
          </RevealItem>
        </RevealGroup>

        <p className="mt-10 text-center">
          <Label>
            Free while it is a hackathon build · no card · no invite · no
            waitlist
          </Label>
        </p>
      </Shell>
    </section>
  )
}
