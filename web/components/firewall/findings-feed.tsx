import { cn } from '@/lib/utils'
import { Display, Label, MetaRule, Shell } from './primitives'

type Tier = 'SAFE' | 'WATCH' | 'DANGEROUS'

type Finding = {
  time: string
  tier: Tier
  /** Share of the wallet's exposure this approval represents, 0–1. */
  exposure: number
  allowance: string
  token: string
  spender: string
}

const FINDINGS: Finding[] = [
  {
    time: '03:14:02',
    tier: 'DANGEROUS',
    exposure: 1,
    allowance: 'Unlimited',
    token: 'USDC',
    spender: '0xbad0…ad00',
  },
  {
    time: '03:14:02',
    tier: 'WATCH',
    exposure: 0.42,
    allowance: '1,000',
    token: 'DAI',
    spender: '0xca11…a110',
  },
  {
    time: '03:14:03',
    tier: 'SAFE',
    exposure: 0.08,
    allowance: '250',
    token: 'USDT',
    spender: '0x5afe…05afe',
  },
  {
    time: '03:14:09',
    tier: 'DANGEROUS',
    exposure: 1,
    allowance: 'Unlimited',
    token: 'WETH',
    spender: '0x9f2c…41ab',
  },
  {
    time: '03:14:11',
    tier: 'WATCH',
    exposure: 0.6,
    allowance: 'Unlimited',
    token: 'ARB',
    spender: '0x1f0b…c4d0',
  },
  {
    time: '03:14:18',
    tier: 'SAFE',
    exposure: 0.05,
    allowance: '40',
    token: 'LINK',
    spender: '0x77aa…31de',
  },
  {
    time: '03:14:24',
    tier: 'DANGEROUS',
    exposure: 0.9,
    allowance: 'Unlimited',
    token: 'COW',
    spender: '0x7e31…09fd',
  },
  {
    time: '03:14:31',
    tier: 'SAFE',
    exposure: 0.03,
    allowance: '12',
    token: 'AAVE',
    spender: '0x2c40…b8a1',
  },
]

const TIER_STYLE: Record<Tier, string> = {
  SAFE: 'border-border/70 text-muted-foreground',
  WATCH: 'border-foreground/30 text-foreground/80',
  DANGEROUS: 'border-foreground bg-foreground text-background',
}

export function FindingsFeed() {
  const dangerous = FINDINGS.filter((f) => f.tier === 'DANGEROUS').length

  return (
    <section className="border-t border-border/60 bg-background py-20 md:py-28">
      <Shell>
        <MetaRule
          dot
          left={`${FINDINGS.length} approvals · 03:14 UTC`}
          center="1 wallet · 5 chains"
          right={`${dangerous} auto-revoked`}
        />

        <div className="mt-10 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <Label>One scan</Label>
          <Display
            lead="What it"
            trail="found"
            className="text-[2.5rem] sm:text-[3.25rem] md:text-[4rem]"
          />
        </div>

        <ul className="mt-12 flex flex-col border-t border-border/60">
          {FINDINGS.map((f) => (
            <li
              key={`${f.time}-${f.spender}`}
              className="group flex items-center gap-4 border-b border-border/60 py-5 transition-colors hover:bg-foreground/[0.03] sm:gap-8"
            >
              <Label className="w-[4.5rem] shrink-0 tabular-nums sm:w-24">
                {f.time}
              </Label>

              <span
                className={cn(
                  'label shrink-0 rounded-full border px-2.5 py-1',
                  TIER_STYLE[f.tier],
                )}
              >
                {f.tier}
              </span>

              <span className="display w-24 shrink-0 text-sm tabular-nums text-foreground md:text-base">
                {f.allowance}
              </span>

              {/* exposure bar */}
              <span
                aria-hidden="true"
                className="hidden h-px min-w-0 flex-1 bg-border/60 sm:block"
              >
                <span
                  className="block h-px bg-foreground/60 transition-all duration-500 group-hover:bg-foreground"
                  style={{ width: `${f.exposure * 100}%` }}
                />
              </span>

              <span className="ml-auto shrink-0 font-mono text-xs text-muted-foreground transition-colors group-hover:text-foreground">
                {f.token} · {f.spender}
              </span>
            </li>
          ))}
        </ul>

        <p className="mt-8 max-w-[62ch] text-pretty text-sm leading-relaxed text-muted-foreground">
          Only the three DANGEROUS rows were acted on. Watch and Safe are left
          exactly where they are — an unverified contract is a reason to look,
          not a reason to spend your gas.
        </p>
      </Shell>
    </section>
  )
}
