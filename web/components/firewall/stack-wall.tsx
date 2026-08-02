import { Display, Label, MetaRule, Shell } from './primitives'

/**
 * Replaces the template's customer-logo wall.
 *
 * That wall listed IBM, Netflix, Stripe and others as customers. Shipping it
 * would put a false claim about real companies on the page — and on a product
 * whose entire pitch is "we only act on verified signals", borrowed logos are
 * the fastest way to lose the reader. What we can honestly show is what the
 * thing is actually built on, and which chains it actually reaches.
 */

const STACK = [
  { name: 'KeeperHub', role: 'onchain execution' },
  { name: 'GoPlus Security', role: 'approval + threat intel' },
  { name: 'Rust · Axum', role: 'API and job orchestration' },
  { name: 'Python · Celery', role: 'scanning worker' },
  { name: 'LangGraph', role: 'durable agent loop' },
  { name: 'PostgreSQL', role: 'findings and audit trail' },
  { name: 'Redis', role: 'queue and checkpoints' },
  { name: 'OpenTelemetry', role: 'traces, metrics, logs' },
]

const CHAINS = ['Ethereum', 'Base', 'Arbitrum', 'Polygon', 'Sepolia']

export function StackWall() {
  return (
    <section className="border-t border-border/60 bg-background py-20 md:py-28">
      <Shell>
        <MetaRule
          dot
          left={`${STACK.length} components · one repository`}
          center="Every dependency is named"
          right={`${CHAINS.length} chains configured`}
        />

        <Display
          lead="Built on things"
          trail="you can check."
          className="mt-12 max-w-[20ch] text-[2.5rem] sm:text-[3.25rem] md:text-[4rem]"
        />

        <ul className="mt-14 grid grid-cols-2 border-l border-t border-border/60 sm:grid-cols-3 lg:grid-cols-4">
          {STACK.map((s) => (
            <li
              key={s.name}
              className="group flex aspect-[3/2] flex-col justify-between border-b border-r border-border/60 p-5 transition-colors hover:bg-foreground/[0.04]"
            >
              <Label className="transition-colors group-hover:text-foreground">
                {s.role}
              </Label>
              <span className="display text-pretty text-xl text-muted-foreground transition-colors group-hover:text-foreground md:text-2xl">
                {s.name}
              </span>
            </li>
          ))}
        </ul>

        <div className="mt-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <Label>
            Chains scanned out of the box · {CHAINS.join(' · ')}
          </Label>
          <Label className="text-foreground">no borrowed logos</Label>
        </div>
      </Shell>
    </section>
  )
}
