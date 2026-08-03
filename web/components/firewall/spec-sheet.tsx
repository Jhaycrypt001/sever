'use client'

import { useState } from 'react'
import { cn } from '@/lib/utils'
import { Reveal, RevealGroup, RevealItem } from '../motion/reveal'
import { ApprovalMap } from './approval-map'
import { Display, Label, MetaRule, Shell } from './primitives'

const SHEET_META = [
  { k: 'Issued', v: '2026.08' },
  { k: 'Chains', v: '5' },
  { k: 'Sheet', v: '1 / 1' },
]

const ZONES = [
  {
    id: 'A',
    tag: 'Source',
    title: 'Every outstanding approval, with its threat signals attached',
    body: 'One GoPlus call per chain returns the spenders your wallet has approved, already flagged for malicious behaviour and unverified source.',
  },
  {
    id: 'B',
    tag: 'Classifier',
    title: 'The risk tier is a pure function, never a model',
    body: 'Safe, Watch, or Dangerous is decided by code you can read in one screen. A language model can describe a finding; it can never promote one into a transaction.',
  },
  {
    id: 'C',
    tag: 'Execution',
    title: 'Dangerous approvals are revoked onchain through KeeperHub',
    body: 'approve(spender, 0), submitted as a direct contract call, polled to confirmation. Gas is sponsored, so the watched wallet needs no balance.',
  },
  {
    id: 'D',
    tag: 'Restraint',
    title: 'Watch and Safe are surfaced, never touched',
    body: 'Only a confirmed malicious signal spends gas. Contradictory intelligence, a spender both trusted and flagged, is shown to you rather than acted on.',
  },
  {
    id: 'E',
    tag: 'Proof',
    title: 'Every attempt leaves a transaction hash or a reason',
    body: 'Findings persist with their tier, status and hash. A dry run is recorded as simulated, so nothing is ever shown as revoked without a transaction behind it.',
  },
  {
    id: 'F',
    tag: 'Autonomy',
    title: 'No API key stands between a wallet and its protection',
    body: 'Scanning, classification and revocation run with no language model at all. An expired key costs you prose, not coverage.',
  },
]

export function SpecSheet() {
  const [active, setActive] = useState('A')

  return (
    <section
      id="product"
      className="border-t border-border/60 bg-background py-20 md:py-28"
    >
      <Shell>
        <Reveal className="flex flex-col gap-6 border-b border-border/60 pb-10 md:flex-row md:items-end md:justify-between">
          <Display
            lead="The firewall,"
            trail="at a glance."
            className="text-[2.5rem] sm:text-[3.25rem] md:text-[4rem]"
          />

          <div className="flex flex-col gap-3 md:items-end">
            <div className="flex items-center gap-3">
              <Label className="text-foreground">§ AFW / 01</Label>
              <Label>Rev A</Label>
            </div>
            <dl className="flex flex-wrap gap-x-6 gap-y-2">
              {SHEET_META.map((m) => (
                <div key={m.k} className="flex items-baseline gap-2">
                  <dt>
                    <Label>{m.k}</Label>
                  </dt>
                  <dd>
                    <Label className="text-foreground">{m.v}</Label>
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </Reveal>

        <div className="mt-10 flex flex-col gap-10 lg:flex-row lg:gap-16">
          {/* plate */}
          <Reveal className="flex w-full shrink-0 flex-col lg:w-[42%]">
            <div className="relative aspect-[4/5] w-full overflow-hidden border-x border-border/60">
              <ApprovalMap />
              <span className="label absolute inset-x-0 top-6 text-center tracking-[0.35em] text-white/85">
                Surface
              </span>
            </div>

            <MetaRule
              className="mt-auto pt-10"
              left="Spec · 6 zones, A through F"
              right="Hover or scroll to inspect"
            />
          </Reveal>

          {/* zone list */}
          <RevealGroup
            className="flex min-w-0 flex-1 flex-col border-t border-border/60"
            stagger={0.07}
          >
            {ZONES.map((z) => {
              const isActive = active === z.id
              return (
                <RevealItem
                  key={z.id}
                  onMouseEnter={() => setActive(z.id)}
                  onFocus={() => setActive(z.id)}
                  className={cn(
                    'group flex flex-col gap-3 border-b border-border/60 py-6 transition-colors sm:flex-row sm:items-baseline sm:gap-8',
                    isActive ? 'bg-foreground/[0.03]' : '',
                  )}
                >
                  <span
                    className={cn(
                      'display shrink-0 text-2xl transition-colors sm:w-10',
                      isActive ? 'text-foreground' : 'text-muted-foreground',
                    )}
                  >
                    {z.id}.
                  </span>

                  <Label
                    className={cn(
                      'shrink-0 sm:w-32',
                      isActive ? 'text-foreground' : '',
                    )}
                  >
                    {z.tag}
                  </Label>

                  <div className="min-w-0 flex-1">
                    <h3 className="text-pretty text-lg font-medium tracking-tight text-foreground md:text-xl">
                      {z.title}
                    </h3>
                    <p className="mt-1.5 text-pretty text-sm leading-relaxed text-muted-foreground">
                      {z.body}
                    </p>
                  </div>
                </RevealItem>
              )
            })}
          </RevealGroup>
        </div>
      </Shell>
    </section>
  )
}
