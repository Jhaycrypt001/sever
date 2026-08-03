import { EXECUTION_MS, GAS_USED } from '@/lib/proof'
import { cn } from '@/lib/utils'
import { Reveal, RevealGroup, RevealItem } from '../motion/reveal'
import { Display, GhostLink, GlowPill, Label, Shell } from './primitives'

/**
 * Every figure here is measured from the first real execution (see lib/proof),
 * or is a structural property of the classifier — never an invented benchmark.
 */
const STATS = [
  {
    value: '7.9s',
    label: 'Detection to confirmed revocation',
    invert: false,
  },
  { value: '0', label: 'Gas paid by the protected wallet', invert: true },
  {
    value: '0',
    label: 'Approvals revoked without a confirmed signal',
    invert: false,
  },
]

export function Bulletin() {
  return (
    <section className="border-t border-border/60 bg-background">
      {/* filing strip */}
      <div className="dot-grid border-b border-border/60 text-foreground/[0.14]">
        <Shell className="py-5">
          <div className="flex flex-col gap-3 md:flex-row md:items-center">
            <div className="flex flex-1 items-center gap-2">
              <span
                aria-hidden="true"
                className="size-1.5 rounded-full bg-foreground/70"
              />
              <Label className="text-foreground">Bulletin № 01</Label>
            </div>
            <Label className="flex-1 md:text-center">
              AFW-2026-08 · Sheet 1/1
            </Label>
            <Label className="flex-1 md:text-right">
              Executed on Sepolia · {GAS_USED} gas
            </Label>
          </div>
        </Shell>
      </div>

      <div className="flex flex-col border-b border-border/60 lg:flex-row">
        {/* headline */}
        <Reveal className="flex min-w-0 flex-1 flex-col justify-center px-6 py-16 md:px-8 md:py-24 lg:border-r lg:border-border/60 lg:pl-[max(2rem,calc((100vw-1232px)/2+2rem))]">
          <Label>Headline · Filed 2026.08.02</Label>

          <Display
            lead="A drain does not start"
            trail="when they take it."
            className="mt-8 max-w-[16ch] text-[2.75rem] sm:text-[3.5rem] md:text-[4.25rem]"
          />

          <p className="mt-8 max-w-[46ch] text-pretty leading-relaxed text-muted-foreground">
            It starts the day you approve the contract. The signature is already
            on chain, sitting there, waiting. Every revoke tool asks you to
            notice first. This one does the noticing, and then does something
            about it, in {EXECUTION_MS} milliseconds, measured end to end.
          </p>
        </Reveal>

        {/* stats stack */}
        <RevealGroup className="flex w-full shrink-0 flex-col lg:w-[42%]" stagger={0.12}>
          {STATS.map((s) => (
            <RevealItem
              key={s.label}
              className={cn(
                'flex flex-1 flex-col justify-center gap-4 border-t border-border/60 px-6 py-12 md:px-12 lg:border-t-0 lg:[&:not(:first-child)]:border-t',
                s.invert
                  ? 'bg-foreground text-background'
                  : 'bg-background text-foreground',
              )}
            >
              <span className="display text-[3.5rem] leading-none md:text-[4.5rem]">
                {s.value}
              </span>
              <Label className={s.invert ? 'text-background/70' : undefined}>
                {s.label}
              </Label>
            </RevealItem>
          ))}
        </RevealGroup>
      </div>

      {/* CTA strip */}
      <div className="dot-grid border-b border-border/60 text-foreground/[0.14]">
        <Shell className="flex flex-col gap-8 py-10 md:flex-row md:items-center md:justify-between">
          <p className="display text-pretty text-xl md:text-2xl">
            Revoking is the easy part.{' '}
            <span className="text-muted-foreground">
              Knowing what to revoke is the product.
            </span>
          </p>
          <div className="flex flex-wrap items-center gap-x-8 gap-y-4">
            <GlowPill href="/console">Scan a wallet</GlowPill>
            <GhostLink href="#evidence">See the transaction</GhostLink>
          </div>
        </Shell>
      </div>
    </section>
  )
}
