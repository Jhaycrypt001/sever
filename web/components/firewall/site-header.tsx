'use client'

import { AnimatePresence, motion, useMotionValueEvent, useReducedMotion, useScroll } from 'motion/react'
import { Menu, X } from 'lucide-react'
import { useState } from 'react'
import { REPO_URL, SEPOLIA_TX_URL } from '@/lib/proof'
import { cn } from '@/lib/utils'

const NAV = [
  { label: 'HOME', href: '#', active: true },
  { label: 'HOW IT WORKS', href: '#product' },
  { label: 'EVIDENCE', href: '#evidence' },
  { label: 'PRICING', href: '#pricing' },
  { label: 'CONSOLE', href: '/console' },
  { label: 'SOURCE', href: REPO_URL },
]

const EASE = [0.16, 1, 0.3, 1] as const

function AnnouncementBanner({ onClose }: { onClose: () => void }) {
  return (
    <div className="relative flex items-center justify-center gap-4 bg-[#fafafa] px-12 py-3 text-[#0a0a0a]">
      <p className="text-center text-sm">
        Every revocation is a real onchain transaction. Here is the first one.
      </p>
      <a
        href={SEPOLIA_TX_URL}
        target="_blank"
        rel="noreferrer"
        className="hidden shrink-0 rounded-full border border-[#0a0a0a]/20 px-4 py-1.5 text-sm font-semibold transition-colors hover:bg-[#0a0a0a] hover:text-[#fafafa] sm:inline-block"
      >
        View on Etherscan
      </a>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close banner"
        className="absolute right-4 top-1/2 -translate-y-1/2 text-[#0a0a0a]/50 transition-colors hover:text-[#0a0a0a]"
      >
        <X className="size-4" strokeWidth={1.5} />
      </button>
    </div>
  )
}

function Wordmark() {
  return (
    <a
      href="/"
      aria-label="Approval Firewall, home"
      // Allowed to shrink: at 320px the wordmark plus the menu button did not
      // fit, and an unshrinkable wordmark pushed the whole page sideways.
      className="flex min-w-0 items-center gap-2 sm:gap-3"
    >
      {/* A shield over a severed link: this revokes, it does not just watch. */}
      <svg
        viewBox="0 0 32 32"
        className="size-6 shrink-0 text-foreground sm:size-7"
        aria-hidden="true"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinecap="round"
      >
        <path d="M16 2.5 4.5 7v9c0 6.6 4.8 11.6 11.5 13.5C22.7 27.6 27.5 22.6 27.5 16V7L16 2.5Z" />
        <path d="M13.4 13 11.8 14.6a3.4 3.4 0 0 0 4.8 4.8l1.6-1.6" />
        <path d="M18.6 19 20.2 17.4a3.4 3.4 0 0 0-4.8-4.8l-1.6 1.6" />
      </svg>
      <span className="display truncate whitespace-nowrap text-[1.125rem] leading-none text-foreground sm:text-[1.375rem] md:text-[1.6875rem]">
        Approval Firewall
      </span>
    </a>
  )
}

/**
 * The header folds away on the way down and unfolds on the way up.
 *
 * Reading direction is the signal: scrolling down means the reader is moving
 * through the page and the chrome is in the way; scrolling up almost always
 * means they are going back for the navigation. A header that merely sticks
 * costs vertical space on every screen for the few seconds anyone wants it.
 *
 * `useScroll` is read through `useMotionValueEvent` rather than a React scroll
 * listener so the sampling stays off the render path, and the direction test
 * carries a small threshold so a trackpad's sub-pixel jitter cannot flap it.
 */
export function SiteHeader() {
  const [bannerOpen, setBannerOpen] = useState(true)
  const [folded, setFolded] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [lastY, setLastY] = useState(0)

  const reduce = useReducedMotion()
  const { scrollY } = useScroll()

  useMotionValueEvent(scrollY, 'change', (y) => {
    const previous = lastY
    setLastY(y)
    setScrolled(y > 24)

    // An open menu must never be folded out from under the reader's finger.
    if (menuOpen) return

    const delta = y - previous
    if (Math.abs(delta) < 6) return
    // Never fold inside the first viewport: the hero CTA lives there and the
    // header is the only other way to reach the console.
    setFolded(delta > 0 && y > 160)
  })

  return (
    <motion.header
      className="fixed inset-x-0 top-0 z-50"
      animate={reduce ? undefined : { y: folded ? '-110%' : '0%' }}
      transition={{ duration: 0.45, ease: EASE }}
    >
      <AnimatePresence initial={false}>
        {bannerOpen && !scrolled ? (
          <motion.div
            key="banner"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.35, ease: EASE }}
            className="overflow-hidden"
          >
            <AnnouncementBanner onClose={() => setBannerOpen(false)} />
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div
        className={cn(
          'mx-auto flex w-full max-w-[1232px] items-center justify-between gap-4 px-6 py-6 transition-colors duration-300 md:px-8',
        )}
      >
        <Wordmark />

        <div
          className={cn(
            'flex items-center rounded-full border p-1 transition-all duration-300',
            scrolled
              ? 'border-border/70 bg-background/70 shadow-lg shadow-black/20 backdrop-blur-xl'
              : 'border-border/70 bg-background/40 backdrop-blur-md',
          )}
        >
          <nav aria-label="Main" className="hidden items-center lg:flex">
            {NAV.map((item) => (
              <a
                key={item.label}
                href={item.href}
                aria-current={item.active ? 'page' : undefined}
                className={cn(
                  'label px-4 py-2.5 transition-colors',
                  item.active
                    ? 'text-foreground'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {item.active ? (
                  <>
                    <span aria-hidden="true" className="mr-1 opacity-60">
                      [
                    </span>
                    {item.label}
                    <span aria-hidden="true" className="ml-1 opacity-60">
                      ]
                    </span>
                  </>
                ) : (
                  item.label
                )}
              </a>
            ))}
          </nav>

          <a
            href="/console"
            className="label ml-2 hidden rounded-full bg-foreground px-5 py-3 text-background transition-opacity hover:opacity-85 sm:inline-block"
          >
            SCAN A WALLET
          </a>

          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            className="ml-1 flex size-11 items-center justify-center rounded-full text-foreground lg:hidden"
          >
            {menuOpen ? (
              <X className="size-5" strokeWidth={1.5} />
            ) : (
              <Menu className="size-5" strokeWidth={1.5} />
            )}
          </button>
        </div>
      </div>

      <AnimatePresence>
        {menuOpen ? (
          <motion.nav
            id="mobile-nav"
            aria-label="Mobile"
            key="mobile-nav"
            initial={{ opacity: 0, y: -12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -12 }}
            transition={{ duration: 0.28, ease: EASE }}
            className="mx-6 overflow-hidden rounded-2xl border border-border/70 bg-background/95 backdrop-blur-xl lg:hidden"
          >
            <ul className="flex flex-col p-2">
              {NAV.map((item) => (
                <li key={item.label}>
                  <a
                    href={item.href}
                    onClick={() => setMenuOpen(false)}
                    className="label block rounded-xl px-4 py-4 text-muted-foreground transition-colors hover:bg-foreground/5 hover:text-foreground"
                  >
                    {item.label}
                  </a>
                </li>
              ))}
              <li className="p-2">
                <a
                  href="/console"
                  onClick={() => setMenuOpen(false)}
                  className="label block rounded-full bg-foreground px-5 py-4 text-center text-background"
                >
                  SCAN A WALLET
                </a>
              </li>
            </ul>
          </motion.nav>
        ) : null}
      </AnimatePresence>
    </motion.header>
  )
}
