'use client'

import { Moon, X } from 'lucide-react'
import { useState } from 'react'
import { REPO_URL, SEPOLIA_TX_URL } from '@/lib/proof'
import { cn } from '@/lib/utils'

const NAV = [
  { label: 'HOME', href: '#', active: true },
  { label: 'HOW IT WORKS', href: '#product' },
  { label: 'EVIDENCE', href: '#evidence' },
  { label: 'PRICING', href: '#pricing' },
  { label: 'SOURCE', href: REPO_URL },
]

function AnnouncementBanner({ onClose }: { onClose: () => void }) {
  return (
    <div className="relative flex items-center justify-center gap-4 bg-[#fafafa] px-12 py-3 text-[#0a0a0a]">
      <p className="text-center text-sm">
        Every revocation is a real onchain transaction — here is the first one.
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
      href="#"
      aria-label="Approval Firewall — home"
      className="flex shrink-0 items-center gap-3"
    >
      {/* A shield over a severed link: this revokes, it does not just watch. */}
      <svg
        viewBox="0 0 32 32"
        className="size-7 text-foreground"
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
      <span className="display flex items-start whitespace-nowrap text-[1.375rem] leading-none text-foreground md:text-[1.6875rem]">
        Approval Firewall
      </span>
    </a>
  )
}

export function SiteHeader() {
  const [bannerOpen, setBannerOpen] = useState(true)
  const [dark, setDark] = useState(true)

  return (
    <header className="absolute inset-x-0 top-0 z-50">
      {bannerOpen ? (
        <AnnouncementBanner onClose={() => setBannerOpen(false)} />
      ) : null}

      <div className="mx-auto flex w-full max-w-[1232px] items-center justify-between gap-4 px-6 py-6 md:px-8">
        <Wordmark />

        <div className="flex items-center rounded-full border border-border/70 bg-background/40 p-1 backdrop-blur-md">
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

          <button
            type="button"
            onClick={() => setDark((v) => !v)}
            className="mx-2 text-muted-foreground transition-colors hover:text-foreground"
          >
            <Moon className="size-4" strokeWidth={1.5} />
            <span className="sr-only">
              {dark ? 'Switch to light theme' : 'Switch to dark theme'}
            </span>
          </button>

          <a
            href="#scan"
            className="label rounded-full bg-foreground px-5 py-3 text-background transition-opacity hover:opacity-85"
          >
            SCAN A WALLET
          </a>
        </div>
      </div>
    </header>
  )
}
