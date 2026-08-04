import Image from 'next/image'
import { Reveal, RevealWords } from '../motion/reveal'
import { GhostLink, GlowPill, Shell } from './primitives'

export function Hero() {
  return (
    <section className="relative isolate overflow-hidden bg-background pb-24 pt-44 md:pb-40 md:pt-52">
      {/* ambient wash */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          background:
            'radial-gradient(120% 80% at 50% 0%, oklch(0.28 0 0) 0%, transparent 62%)',
        }}
      />

      <Shell className="relative z-10">
        <h1 className="display max-w-[18ch] text-balance text-[3.25rem] leading-[0.94] text-foreground sm:text-[4.5rem] md:text-[5.5rem] lg:text-[6.75rem]">
          <RevealWords text="The approval that drains you is already signed" />
        </h1>

        <Reveal delay={0.35}>
          <p className="mt-8 max-w-[46ch] text-pretty text-base leading-relaxed text-muted-foreground md:text-lg">
            Sever finds the malicious token approvals sitting in
            your wallet and revokes them onchain through KeeperHub, without
            waiting for you to notice.
          </p>

          <div className="mt-10 flex flex-wrap items-center gap-x-8 gap-y-4">
            <GlowPill href="/console">Scan a wallet</GlowPill>
            <GhostLink href="#evidence">See the transaction</GhostLink>
          </div>
        </Reveal>
      </Shell>

      {/*
        The console, photographed rather than drawn.
        `public/console.png` is a Playwright screenshot of this product doing
        a live scan of a real wallet on mainnet — captured by
        `e2e/capture-console.spec.ts`, which asserts the dangerous finding is
        on screen before it saves.

        It replaced a hand-built mock. Every figure in that mock was invented,
        and a "sample data" caption does not really fix a fabricated panel when
        the product it depicts exists and works. Regenerate it rather than
        edit it.
      */}
      <div
        aria-hidden="true"
        className="pointer-events-none relative mt-[-2rem] h-[420px] select-none md:h-[560px] lg:h-[680px]"
        style={{ perspective: '1800px' }}
      >
        <div
          className="absolute left-1/2 top-16 origin-top"
          style={{
            transform:
              'translateX(-50%) rotateX(52deg) rotateZ(-14deg) scale(0.82)',
            transformStyle: 'preserve-3d',
            maskImage:
              'linear-gradient(to bottom, #000 0%, #000 55%, transparent 92%)',
            WebkitMaskImage:
              'linear-gradient(to bottom, #000 0%, #000 55%, transparent 92%)',
          }}
        >
          <Image
            src="/console.png"
            alt=""
            width={1440}
            height={1100}
            priority
            className="w-[1440px] max-w-none rounded-xl border border-white/10 shadow-2xl"
          />
        </div>
      </div>

      <Shell className="relative z-10 -mt-10 md:-mt-16">
        <p className="label text-center text-muted-foreground">
          A real scan of a real wallet · one dangerous approval found on Base
        </p>
      </Shell>
    </section>
  )
}
