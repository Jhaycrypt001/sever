'use client'

import { BatteryFull, Signal, Wifi } from 'lucide-react'
import {
  AnimatePresence,
  motion,
  useMotionValueEvent,
  useReducedMotion,
  useScroll,
  useSpring,
} from 'motion/react'
import { useRef, useState } from 'react'
import { GAS_USED, truncateHash, SEPOLIA_TX_HASH } from '@/lib/proof'
import { cn } from '@/lib/utils'
import { Reveal } from '../motion/reveal'
import { Display, Label, Shell } from './primitives'

const STEPS = [
  {
    n: '01',
    tag: 'Detect',
    title: 'A new approval appears at 03:14.',
    body: 'A recurring scan reads every outstanding approval on each configured chain and compares it against what previous runs already saw. This spender is new.',
  },
  {
    n: '02',
    tag: 'Classify',
    title: 'Threat intelligence returns a malicious flag.',
    body: 'The spender is on a phishing list and holds an unlimited allowance. A pure function, not a model, reads those signals and returns DANGEROUS.',
  },
  {
    n: '03',
    tag: 'Revoke',
    title: 'KeeperHub broadcasts the revocation.',
    body: 'approve(spender, 0) goes out as a direct contract call from the wallet delegated to KeeperHub, with gas sponsored. You sign nothing and pay nothing.',
  },
  {
    n: '04',
    tag: 'Prove',
    title: 'A transaction hash, not a reassurance.',
    body: 'The status is polled to confirmation and stored with its hash. Nothing is ever marked revoked without a transaction you can open on a block explorer.',
  },
]

const EASE = [0.16, 1, 0.3, 1] as const

function PhoneMock({ step }: { step: number }) {
  return (
    <div
      aria-hidden="true"
      className="relative mx-auto w-full max-w-[260px] select-none rounded-[2.25rem] border border-border/70 bg-[#0b0b0b] p-2 shadow-[0_40px_80px_-30px_rgba(0,0,0,0.9)] lg:max-w-[320px] lg:rounded-[2.75rem] lg:p-2.5"
    >
      <div className="relative overflow-hidden rounded-[1.9rem] bg-[#050505] lg:rounded-[2.25rem]">
        {/* status bar */}
        <div className="flex items-center justify-between px-5 pt-3 text-white/60 lg:px-6 lg:pt-4">
          <span className="font-mono text-[10px]">03:14</span>
          <div className="flex items-center gap-1.5">
            <Signal className="size-3" strokeWidth={2} />
            <Wifi className="size-3" strokeWidth={2} />
            <span className="font-mono text-[10px]">100%</span>
            <BatteryFull className="size-3.5" strokeWidth={1.5} />
          </div>
        </div>

        {/* dynamic island */}
        <div className="mx-auto mt-2.5 flex w-[120px] items-center justify-center rounded-full bg-black py-1.5 lg:mt-3 lg:w-[132px] lg:py-2">
          <span className="size-1.5 rounded-full bg-white/20" />
        </div>

        <div className="px-3.5 pb-5 pt-5 lg:px-4 lg:pb-8 lg:pt-8">
          <p className="text-center font-mono text-[10px] uppercase tracking-[0.14em] text-white/35">
            Sunday, August 2
          </p>
          <p className="mt-1 text-center font-sans text-[2.5rem] font-light leading-none tracking-tight text-white lg:text-[3.25rem]">
            03:14
          </p>

          {/* The card is the payload of the step: it fills in as the agent
              works, so what is on screen always matches the numbered stage. */}
          <div className="mt-5 rounded-2xl border border-white/10 bg-white/[0.06] p-3.5 backdrop-blur lg:mt-8 lg:p-4">
            <div className="flex items-center gap-2">
              <span className="flex size-5 items-center justify-center rounded-md bg-white/10">
                <svg
                  viewBox="0 0 32 32"
                  className="size-3 text-white/70"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                >
                  <path d="M16 2.5 4.5 7v9c0 6.6 4.8 11.6 11.5 13.5C22.7 27.6 27.5 22.6 27.5 16V7L16 2.5Z" />
                  <path d="M13.4 13 11.8 14.6a3.4 3.4 0 0 0 4.8 4.8l1.6-1.6" />
                  <path d="M18.6 19 20.2 17.4a3.4 3.4 0 0 0-4.8-4.8l-1.6 1.6" />
                </svg>
              </span>
              <span className="text-[11px] font-medium text-white/80">
                Sever
              </span>
              <motion.span
                key={step >= 1 ? 'flagged' : 'scanning'}
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.3 }}
                className={cn(
                  'ml-auto rounded px-1.5 py-0.5 font-mono text-[9px]',
                  step >= 1
                    ? 'bg-red-500/20 text-red-200/85'
                    : 'bg-white/10 text-white/50',
                )}
              >
                {step >= 1 ? 'DANGEROUS' : 'SCANNING'}
              </motion.span>
            </div>

            <p className="mt-3 text-sm font-medium leading-snug text-white">
              {step >= 2
                ? 'Unlimited USDC approval revoked'
                : 'Unlimited USDC approval found'}
            </p>
            <p className="mt-0.5 font-mono text-[10px] text-white/45">
              0xbad0…ad00 · phishing_activities
            </p>

            <AnimatePresence mode="wait">
              {step >= 3 ? (
                <motion.div
                  key="receipt"
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  transition={{ duration: 0.35, ease: EASE }}
                  className="overflow-hidden"
                >
                  <div className="mt-4 flex items-end justify-between">
                    <div>
                      <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-white/40">
                        Transaction
                      </p>
                      <p className="mt-1 font-mono text-xs text-white">
                        {truncateHash(SEPOLIA_TX_HASH, 10, 6)}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="font-mono text-[10px] uppercase tracking-[0.1em] text-white/40">
                        Gas
                      </p>
                      <p className="mt-1 font-mono text-xs text-white/80">
                        {GAS_USED}
                      </p>
                    </div>
                  </div>
                </motion.div>
              ) : null}
            </AnimatePresence>

            <div className="mt-4 flex gap-2">
              <button
                type="button"
                tabIndex={-1}
                className="flex-1 rounded-full bg-white py-2 text-[11px] font-semibold text-black"
              >
                View on Etherscan
              </button>
              <button
                type="button"
                tabIndex={-1}
                className="flex-1 rounded-full border border-white/15 py-2 text-[11px] font-medium text-white/80"
              >
                Show journal
              </button>
            </div>
          </div>
        </div>

        {/* home bar */}
        <div className="mx-auto mb-2.5 h-1 w-24 rounded-full bg-white/20 lg:w-28" />
      </div>

      <div className="mt-4 flex items-center justify-between lg:mt-6">
        <Label>Now showing · {STEPS[step].tag}</Label>
        <Label className="text-foreground">Step {step + 1} / 4</Label>
      </div>
    </div>
  )
}

/**
 * Scroll-linked walkthrough.
 *
 * The section is four viewports tall and its contents are pinned, so the page
 * scroll is what advances the stage. The alternative, a hover or click list,
 * asks the reader to drive a story they have not been told yet; here the four
 * stages arrive in the order the agent actually performs them, at reading
 * pace, and scrolling back rewinds it.
 */
export function NightShift() {
  const track = useRef<HTMLDivElement>(null)
  const [step, setStep] = useState(0)
  const reduce = useReducedMotion()

  const { scrollYProgress } = useScroll({
    target: track,
    offset: ['start start', 'end end'],
  })

  // Springing the bar keeps it from twitching on a high-resolution trackpad.
  const progress = useSpring(scrollYProgress, {
    stiffness: 180,
    damping: 32,
    restDelta: 0.001,
  })

  useMotionValueEvent(scrollYProgress, 'change', (value) => {
    const next = Math.min(
      STEPS.length - 1,
      Math.max(0, Math.floor(value * STEPS.length)),
    )
    setStep(next)
  })

  // With reduced motion the pinning goes too: a four-viewport scroll hijack is
  // exactly the kind of thing that setting is asking us not to do.
  if (reduce) {
    return (
      <section className="border-t border-border/60 bg-background py-20 md:py-28">
        <Shell>
          <Intro />
          <div className="mt-14 flex flex-col gap-12 lg:flex-row lg:gap-20">
            <ol className="flex min-w-0 flex-1 flex-col border-t border-border/60">
              {STEPS.map((s) => (
                <li
                  key={s.n}
                  className="flex flex-col gap-2 border-b border-border/60 py-7"
                >
                  <Label className="text-foreground">
                    {s.n} · {s.tag}
                  </Label>
                  <h3 className="display text-xl text-foreground md:text-2xl">
                    {s.title}
                  </h3>
                  <p className="max-w-[52ch] text-pretty text-sm leading-relaxed text-muted-foreground">
                    {s.body}
                  </p>
                </li>
              ))}
            </ol>
            <div className="w-full shrink-0 lg:w-[36%]">
              <PhoneMock step={3} />
            </div>
          </div>
        </Shell>
      </section>
    )
  }

  return (
    <section className="border-t border-border/60 bg-background">
      <Shell className="pt-20 md:pt-28">
        <Reveal>
          <Intro />
        </Reveal>
      </Shell>

      {/* One viewport of scroll per stage, plus a little run-out. */}
      <div ref={track} className="relative h-[420vh]">
        <div className="sticky top-0 flex h-[100svh] items-center overflow-hidden">
          <Shell className="w-full py-10">
            {/* progress rail */}
            <div className="mb-8 flex items-center gap-4">
              <Label className="shrink-0">§ Night shift · 03:14 UTC</Label>
              <div className="h-px flex-1 bg-border/60">
                <motion.div
                  className="h-px origin-left bg-foreground"
                  style={{ scaleX: progress }}
                />
              </div>
              <Label className="shrink-0 text-foreground">
                {String(step + 1).padStart(2, '0')} / 04
              </Label>
            </div>

            <div className="flex flex-col gap-8 lg:flex-row lg:items-center lg:gap-20">
              <ol className="flex min-w-0 flex-1 flex-col">
                {STEPS.map((s, i) => {
                  const isActive = step === i
                  return (
                    <li
                      key={s.n}
                      aria-current={isActive ? 'step' : undefined}
                      className={cn(
                        'border-b border-border/60 first:border-t',
                        // On a phone there is no room for four stages at once,
                        // so only the live one is mounted.
                        isActive ? 'block' : 'hidden lg:block',
                      )}
                    >
                      <motion.div
                        animate={{ opacity: isActive ? 1 : 0.35 }}
                        transition={{ duration: 0.4, ease: EASE }}
                        className="flex flex-col gap-2 py-5 lg:py-6"
                      >
                        <div className="flex items-center gap-3">
                          <Label
                            className={isActive ? 'text-foreground' : undefined}
                          >
                            {s.n} · {s.tag}
                          </Label>
                          {isActive ? (
                            <motion.span
                              layoutId="step-dot"
                              className="size-1.5 rounded-full bg-foreground"
                            />
                          ) : null}
                        </div>
                        <h3 className="display text-xl text-foreground md:text-2xl">
                          {s.title}
                        </h3>
                        <AnimatePresence initial={false}>
                          {isActive ? (
                            <motion.p
                              initial={{ opacity: 0, height: 0 }}
                              animate={{ opacity: 1, height: 'auto' }}
                              exit={{ opacity: 0, height: 0 }}
                              transition={{ duration: 0.4, ease: EASE }}
                              className="max-w-[52ch] overflow-hidden text-pretty text-sm leading-relaxed text-muted-foreground"
                            >
                              {s.body}
                            </motion.p>
                          ) : null}
                        </AnimatePresence>
                      </motion.div>
                    </li>
                  )
                })}
              </ol>

              <div className="w-full shrink-0 lg:w-[34%]">
                <PhoneMock step={step} />
              </div>
            </div>
          </Shell>
        </div>
      </div>
    </section>
  )
}

function Intro() {
  return (
    <>
      <Display
        lead="Eight seconds,"
        trail="and no signature from you."
        className="max-w-[18ch] text-[2.75rem] sm:text-[3.5rem] md:text-[4.5rem]"
      />

      <p className="mt-8 max-w-[52ch] text-pretty leading-relaxed text-muted-foreground">
        Scanning works on any address you paste. Revoking requires signing
        authority, so it runs on the wallet you delegate to KeeperHub, which is
        also why the gas is not yours to pay.
      </p>
    </>
  )
}
