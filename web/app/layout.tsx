import type { Metadata, Viewport } from 'next'
import { DM_Sans, JetBrains_Mono, Space_Grotesk } from 'next/font/google'
import './globals.css'

const dmSans = DM_Sans({
  subsets: ['latin'],
  variable: '--font-dm-sans',
  display: 'swap',
})

const spaceGrotesk = Space_Grotesk({
  subsets: ['latin'],
  variable: '--font-space-grotesk',
  display: 'swap',
})

const jetBrainsMono = JetBrains_Mono({
  subsets: ['latin'],
  variable: '--font-jetbrains-mono',
  display: 'swap',
})

/**
 * Every route renders per request, because the CSP is nonce-based (ADR-061).
 *
 * A nonce is minted per request in `proxy.ts`, but a prerendered page has its
 * nonce baked in at build time, so the two never match and the browser blocks
 * every script on the page. That is not a degraded experience, it is a blank
 * one, and it fails silently: the header is present, the HTML is correct, and
 * nothing runs.
 *
 * Next only opts a route out of static rendering when the route itself reads
 * headers, which these do not, so it has to be declared. The cost is a
 * server render of a page that was already cheap; the alternative is dropping
 * the nonce for `unsafe-inline`, which is the wrong trade on a surface that
 * holds a live access token.
 */
export const dynamic = 'force-dynamic'

export const metadata: Metadata = {
  title:
    'Sever · the agent that revokes the approval draining your wallet',
  description:
    'Sever scans a wallet for malicious ERC-20 approvals and revokes the dangerous ones onchain through KeeperHub, before a drainer gets to use them.',
  // The shield over a severed link, matching the header wordmark. The PNGs
  // are per-scheme because a single fixed colour vanishes into one of the two
  // tab strips; the SVG carries its own media query and is preferred wherever
  // it is supported.
  icons: {
    icon: [
      {
        url: '/icon-light-32x32.png',
        media: '(prefers-color-scheme: light)',
      },
      {
        url: '/icon-dark-32x32.png',
        media: '(prefers-color-scheme: dark)',
      },
      {
        url: '/icon.svg',
        type: 'image/svg+xml',
      },
    ],
    apple: '/apple-icon.png',
  },
}

export const viewport: Viewport = {
  colorScheme: 'dark light',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#ffffff' },
    { media: '(prefers-color-scheme: dark)', color: '#0a0a0a' },
  ],
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html
      lang="en"
      className={`bg-background ${dmSans.variable} ${spaceGrotesk.variable} ${jetBrainsMono.variable}`}
    >
      <body className="antialiased">{children}</body>
    </html>
  )
}
