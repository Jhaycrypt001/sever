import { Bulletin } from '@/components/firewall/bulletin'
import { Evidence } from '@/components/firewall/evidence'
import { FindingsFeed } from '@/components/firewall/findings-feed'
import { Hero } from '@/components/firewall/hero'
import { NightShift } from '@/components/firewall/night-shift'
import { Pricing } from '@/components/firewall/pricing'
import { SiteFooter } from '@/components/firewall/site-footer'
import { SiteHeader } from '@/components/firewall/site-header'
import { SpecSheet } from '@/components/firewall/spec-sheet'
import { StackWall } from '@/components/firewall/stack-wall'

export default function Page() {
  return (
    <div className="dark min-h-screen bg-background text-foreground">
      <SiteHeader />
      <main>
        <Hero />
        <SpecSheet />
        <Bulletin />
        <NightShift />
        <FindingsFeed />
        <Evidence />
        <StackWall />
        <Pricing />
      </main>
      <SiteFooter />
    </div>
  )
}
