import { DashboardMock } from './dashboard-mock'
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
          The approval that drains you is already signed
        </h1>

        <p className="mt-8 max-w-[46ch] text-pretty text-base leading-relaxed text-muted-foreground md:text-lg">
          Approval Firewall finds the malicious token approvals sitting in your
          wallet and revokes them onchain through KeeperHub — without waiting
          for you to notice.
        </p>

        <div className="mt-10 flex flex-wrap items-center gap-x-8 gap-y-4">
          <GlowPill href="#scan">Scan a wallet</GlowPill>
          <GhostLink href="#evidence">See the transaction</GhostLink>
        </div>
      </Shell>

      {/* tilted console */}
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
          <DashboardMock />
        </div>
      </div>
    </section>
  )
}
