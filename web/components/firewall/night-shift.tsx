'use client'

import { BatteryFull, Signal, Wifi } from 'lucide-react'
import { useState } from 'react'
import { GAS_USED, truncateHash, SEPOLIA_TX_HASH } from '@/lib/proof'
import { cn } from '@/lib/utils'
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
    body: 'The spender is on a phishing list and holds an unlimited allowance. A pure function — not a model — reads those signals and returns DANGEROUS.',
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

function PhoneMock({ step }: { step: number }) {
  return (
    <div
      aria-hidden="true"
      className="relative mx-auto w-full max-w-[320px] select-none rounded-[2.75rem] border border-border/70 bg-[#0b0b0b] p-2.5 shadow-[0_40px_80px_-30px_rgba(0,0,0,0.9)]"
    >
      <div className="relative overflow-hidden rounded-[2.25rem] bg-[#050505]">
        {/* status bar */}
        <div className="flex items-center justify-between px-6 pt-4 text-white/60">
          <span className="font-mono text-[10px]">03:14</span>
          <div className="flex items-center gap-1.5">
            <Signal className="size-3" strokeWidth={2} />
            <Wifi className="size-3" strokeWidth={2} />
            <span className="font-mono text-[10px]">100%</span>
            <BatteryFull className="size-3.5" strokeWidth={1.5} />
          </div>
        </div>

        {/* dynamic island */}
        <div className="mx-auto mt-3 flex w-[132px] items-center justify-center rounded-full bg-black py-2">
          <span className="size-1.5 rounded-full bg-white/20" />
        </div>

        <div className="px-4 pb-8 pt-8">
          <p className="text-center font-mono text-[10px] uppercase tracking-[0.14em] text-white/35">
            Sunday, August 2
          </p>
          <p className="mt-1 text-center font-sans text-[3.25rem] font-light leading-none tracking-tight text-white">
            03:14
          </p>

          {/* revocation card */}
          <div className="mt-8 rounded-2xl border border-white/10 bg-white/[0.06] p-4 backdrop-blur">
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
                Approval Firewall
              </span>
              <span className="ml-auto rounded bg-red-500/20 px-1.5 py-0.5 font-mono text-[9px] text-red-200/85">
                DANGEROUS
              </span>
            </div>

            <p className="mt-3 text-sm font-medium leading-snug text-white">
              Unlimited USDC approval revoked
            </p>
            <p className="mt-0.5 font-mono text-[10px] text-white/45">
              0xbad0…ad00 · phishing_activities
            </p>

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
        <div className="mx-auto mb-2.5 h-1 w-28 rounded-full bg-white/20" />
      </div>

      <div className="mt-6 flex items-center justify-between">
        <Label>Now showing · {STEPS[step].tag}</Label>
        <Label className="text-foreground">Step {step + 1} / 4</Label>
      </div>
    </div>
  )
}

export function NightShift() {
  const [step, setStep] = useState(0)

  return (
    <section className="border-t border-border/60 bg-background py-20 md:py-28">
      <Shell>
        <Label>§ Night shift · 03:14 UTC</Label>

        <Display
          lead="Eight seconds,"
          trail="and no signature from you."
          className="mt-8 max-w-[18ch] text-[2.75rem] sm:text-[3.5rem] md:text-[4.5rem]"
        />

        <p className="mt-8 max-w-[52ch] text-pretty leading-relaxed text-muted-foreground">
          Scanning works on any address you paste. Revoking requires signing
          authority, so it runs on the wallet you delegate to KeeperHub — which
          is also why the gas is not yours to pay.
        </p>

        <div className="mt-16 flex flex-col gap-14 lg:flex-row lg:gap-20">
          <ol className="flex min-w-0 flex-1 flex-col border-t border-border/60">
            {STEPS.map((s, i) => {
              const isActive = step === i
              return (
                <li key={s.n} className="border-b border-border/60">
                  <button
                    type="button"
                    onClick={() => setStep(i)}
                    onMouseEnter={() => setStep(i)}
                    aria-current={isActive ? 'step' : undefined}
                    className={cn(
                      'flex w-full flex-col gap-2 py-7 text-left transition-colors',
                      isActive ? 'opacity-100' : 'opacity-55 hover:opacity-90',
                    )}
                  >
                    <Label className={isActive ? 'text-foreground' : undefined}>
                      {s.n} · {s.tag}
                    </Label>
                    <h3 className="display text-xl text-foreground md:text-2xl">
                      {s.title}
                    </h3>
                    <p className="max-w-[52ch] text-pretty text-sm leading-relaxed text-muted-foreground">
                      {s.body}
                    </p>
                  </button>
                </li>
              )
            })}
          </ol>

          <div className="w-full shrink-0 lg:w-[36%]">
            <PhoneMock step={step} />
          </div>
        </div>
      </Shell>
    </section>
  )
}
