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
        {/*
          Leading opens up slightly on small screens: at 0.94 the descenders
          of "you", "already" and "signed" were cut off by the following
          line, which reads as a broken font rather than a tight setting.
          The display sizes keep the tighter figure, where the line box is
          large enough to contain them.
        */}
        <h1 className="display max-w-[18ch] text-balance text-[3.25rem] leading-[1.04] text-foreground sm:text-[4.5rem] sm:leading-[0.94] md:text-[5.5rem] lg:text-[6.75rem]">
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
      {/*
        The tilt is a desktop composition and does not survive a phone.

        The screenshot is 1440px of console laid out for a wide window. Held
        at that width and rotated on a 390px screen, the viewport crops a
        strip out of the middle of it: the reader gets fragments of two cards
        and a line of text, not a product. The perspective made it worse by
        throwing the crop off-axis.

        So below `md` the image simply fits the screen, flat and whole, and
        the tilt is applied only where there is width to carry it. A legible
        small picture of the real console beats a dramatic angle on a
        fragment of it.
      */}
      <div
        aria-hidden="true"
        className="pointer-events-none relative mt-4 select-none px-6 md:mt-[-2rem] md:h-[560px] md:px-0 lg:h-[680px]"
        style={{ perspective: '1800px' }}
      >
        <div
          className="origin-top md:absolute md:left-1/2 md:top-16 md:[transform:translateX(-50%)_rotateX(52deg)_rotateZ(-14deg)_scale(0.82)] md:[transform-style:preserve-3d] md:[mask-image:linear-gradient(to_bottom,#000_0%,#000_55%,transparent_92%)] md:[-webkit-mask-image:linear-gradient(to_bottom,#000_0%,#000_55%,transparent_92%)]"
        >
          <Image
            src="/console.png"
            alt=""
            width={1440}
            height={1100}
            priority
            sizes="(max-width: 768px) 100vw, 1440px"
            className="mx-auto h-auto w-full rounded-xl border border-white/10 shadow-2xl md:w-[1440px] md:max-w-none"
          />
        </div>
      </div>

      {/*
        The caption is pulled up over the faded foot of the tilted image on
        desktop; on mobile the image is flat and opaque to its last row, so
        the same negative margin would print this line straight across a
        finding card.
      */}
      <Shell className="relative z-10 mt-8 md:-mt-16">
        <p className="label text-center text-muted-foreground">
          A real scan of a real wallet · one dangerous approval found on Base
        </p>
      </Shell>
    </section>
  )
}
