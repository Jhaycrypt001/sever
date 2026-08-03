import {
  EXECUTION_MS,
  EXECUTOR_WALLET,
  GAS_USED,
  REPO_URL,
  SEPOLIA_TX_HASH,
  SEPOLIA_TX_URL,
  truncateHash,
} from '@/lib/proof'
import { RevealGroup, RevealItem } from '../motion/reveal'
import { Label, MetaRule, Shell } from './primitives'

/**
 * Replaces the template's testimonial wall.
 *
 * A security product cannot open with invented quotes from people who do not
 * exist — the claim being made is "trust us with signing authority". So this
 * section carries only artifacts a stranger can verify without asking us:
 * a transaction on a public explorer, the decision rule in the source, and
 * the repository itself.
 */

function Receipt({ children }: { children: React.ReactNode }) {
  return (
    <div className="relative aspect-[4/5] w-full overflow-hidden border-b border-border/60 bg-[#0a0a0a]">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            'radial-gradient(90% 60% at 50% 0%, oklch(0.24 0 0) 0%, transparent 70%)',
        }}
      />
      <div className="relative flex h-full flex-col justify-center gap-3 p-6 font-mono text-[11px] leading-relaxed text-white/70">
        {children}
      </div>
    </div>
  )
}

function Row({ k, v, strong }: { k: string; v: string; strong?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-white/[0.07] pb-2">
      <span className="shrink-0 text-[9px] uppercase tracking-[0.12em] text-white/35">
        {k}
      </span>
      <span
        className={
          strong ? 'truncate text-white' : 'truncate text-white/70'
        }
      >
        {v}
      </span>
    </div>
  )
}

export function Evidence() {
  return (
    <section
      id="evidence"
      className="border-t border-border/60 bg-background py-20 md:py-28"
    >
      <Shell>
        <MetaRule
          dot
          left="Evidence · three artifacts"
          center="Independently verifiable"
          right="No testimonials"
        />

        <RevealGroup className="mt-12 grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3" stagger={0.12}>
          {/* 1: the transaction */}
          <RevealItem className="flex h-full flex-col border border-border/60 bg-card/40">
            <Receipt>
              <p className="mb-1 text-[9px] uppercase tracking-[0.2em] text-white/40">
                Sepolia · confirmed
              </p>
              <Row k="Method" v="approve(spender, 0)" strong />
              <Row k="Tx" v={truncateHash(SEPOLIA_TX_HASH, 12, 8)} strong />
              <Row k="From" v={truncateHash(EXECUTOR_WALLET, 10, 6)} />
              <Row k="Gas used" v={GAS_USED} />
              <Row k="Duration" v={`${EXECUTION_MS} ms`} />
              <Row k="Sponsored" v="yes · wallet paid 0" />
            </Receipt>

            <figure className="flex flex-1 flex-col p-6">
              <blockquote className="flex-1 text-pretty text-lg leading-snug tracking-tight text-foreground">
                A real revocation, broadcast through KeeperHub and confirmed on
                chain. Open it yourself; nothing here is a screenshot.
              </blockquote>

              <figcaption className="mt-8 flex flex-wrap items-center gap-2 border-t border-border/60 pt-4">
                <Label className="text-foreground">Artifact 01</Label>
                <Label aria-hidden="true">·</Label>
                <Label>Onchain transaction</Label>
              </figcaption>

              <a
                href={SEPOLIA_TX_URL}
                target="_blank"
                rel="noreferrer"
                className="label mt-4 text-foreground underline-offset-4 transition-opacity hover:opacity-60"
              >
                View on Etherscan →
              </a>
            </figure>
          </RevealItem>

          {/* 2: the decision rule */}
          <RevealItem className="flex h-full flex-col border border-border/60 bg-card/40">
            <Receipt>
              <p className="mb-1 text-[9px] uppercase tracking-[0.2em] text-white/40">
                domain/models.py
              </p>
              <pre className="overflow-hidden whitespace-pre-wrap text-[10px] leading-[1.7] text-white/75">
{`def classify_risk(approval):
    raw = approval.raw
    trusted = flag_state(
        raw.get("trust_list")) is True

    if flag_state(
        raw.get("malicious_address")
    ) is True:
        return (WATCH if trusted
                else DANGEROUS)

    if flag_state(
        raw.get("is_open_source")
    ) is not True:
        return WATCH

    return SAFE`}
              </pre>
            </Receipt>

            <figure className="flex flex-1 flex-col p-6">
              <blockquote className="flex-1 text-pretty text-lg leading-snug tracking-tight text-foreground">
                The entire rule that can authorise spending your gas. No model
                sits in this path. A language model may describe a finding, but
                it can never create one.
              </blockquote>

              <figcaption className="mt-8 flex flex-wrap items-center gap-2 border-t border-border/60 pt-4">
                <Label className="text-foreground">Artifact 02</Label>
                <Label aria-hidden="true">·</Label>
                <Label>Deterministic classifier</Label>
              </figcaption>

              <a
                href={`${REPO_URL}/blob/main/agent/src/aiagent/domain/models.py`}
                target="_blank"
                rel="noreferrer"
                className="label mt-4 text-foreground underline-offset-4 transition-opacity hover:opacity-60"
              >
                Read the source →
              </a>
            </figure>
          </RevealItem>

          {/* 3: the repository */}
          <RevealItem className="flex h-full flex-col border border-border/60 bg-card/40">
            <Receipt>
              <p className="mb-1 text-[9px] uppercase tracking-[0.2em] text-white/40">
                Repository
              </p>
              <Row k="Backend" v="Rust · Axum · hexagonal" strong />
              <Row k="Agent" v="Python · Celery · LangGraph" strong />
              <Row k="Tests" v="209 python · 153 rust" />
              <Row k="Decisions" v="60 ADRs, dated" />
              <Row k="Migrations" v="0001 – 0012" />
              <Row k="Fakes in prod" v="refused at boot" />
            </Receipt>

            <figure className="flex flex-1 flex-col p-6">
              <blockquote className="flex-1 text-pretty text-lg leading-snug tracking-tight text-foreground">
                Every architectural decision is written down and dated, including
                the ones we got wrong and revisited. Read the audit that found
                four bugs before the UI existed.
              </blockquote>

              <figcaption className="mt-8 flex flex-wrap items-center gap-2 border-t border-border/60 pt-4">
                <Label className="text-foreground">Artifact 03</Label>
                <Label aria-hidden="true">·</Label>
                <Label>Open source</Label>
              </figcaption>

              <a
                href={`${REPO_URL}/blob/main/docs/ARCHITECTURE.md`}
                target="_blank"
                rel="noreferrer"
                className="label mt-4 text-foreground underline-offset-4 transition-opacity hover:opacity-60"
              >
                Read the decision log →
              </a>
            </figure>
          </RevealItem>
        </RevealGroup>
      </Shell>
    </section>
  )
}
