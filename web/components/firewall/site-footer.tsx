import { MAINNET_TX_URL, REPO_URL } from '@/lib/proof'
import { FoldingWord } from './folding-word'
import { GlowButton, Label, MetaRule, Shell } from './primitives'

const COLUMNS = [
  {
    title: 'product',
    links: [
      { label: 'How it works', href: '#product' },
      { label: 'Findings', href: '#scan' },
      { label: 'Pricing', href: '#pricing' },
    ],
  },
  {
    title: 'evidence',
    links: [
      { label: 'The transaction', href: MAINNET_TX_URL },
      { label: 'Decision log', href: `${REPO_URL}/blob/main/docs/ARCHITECTURE.md` },
      { label: 'Source', href: REPO_URL },
    ],
  },
  {
    // No "Terms" and no "Privacy": both linked to `#`, which promises legal
    // documents that do not exist. A dead link where a policy should be is
    // worse than no link — it implies someone wrote one. What we can point at
    // truthfully is the licence and the code that handles the data.
    title: 'legal',
    links: [
      { label: 'Licence (MIT)', href: `${REPO_URL}/blob/main/LICENSE` },
      { label: 'What we store', href: `${REPO_URL}/blob/main/docs/ARCHITECTURE.md` },
    ],
  },
]

export function SiteFooter() {
  return (
    <footer
      id="scan"
      className="relative isolate overflow-hidden border-t border-border/60 bg-background pb-14 pt-20 md:pt-28"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          background:
            'radial-gradient(80% 60% at 50% 30%, oklch(0.24 0 0) 0%, transparent 70%)',
        }}
      />

      <Shell>
        <MetaRule
          dot
          left="End of dispatch"
          center="The approval you forgot is still live"
          right="2026.08"
        />

        <FoldingWord
          word="FIREWALL."
          label="Approval Firewall"
          className="display mt-12 flex w-full justify-between text-foreground"
        />

        <p className="mx-auto mt-12 max-w-[52ch] text-pretty text-center leading-relaxed text-muted-foreground">
          Paste an address and see every approval it has ever granted, sorted
          most dangerous first. Scanning is read-only and needs nothing from
          you but the address.
        </p>

        {/* A real GET to the console, which pre-fills from ?address=. */}
        <form
          action="/console"
          method="get"
          className="mx-auto mt-10 flex max-w-lg flex-col items-center gap-3 sm:flex-row sm:justify-center"
        >
          <label htmlFor="scan-address" className="sr-only">
            Wallet address
          </label>
          <input
            id="scan-address"
            type="text"
            name="address"
            required
            inputMode="text"
            autoComplete="off"
            spellCheck={false}
            pattern="0x[a-fA-F0-9]{40}"
            placeholder="0x…"
            className="h-[52px] w-full min-w-0 rounded-full border border-border/70 bg-card/40 px-6 font-mono text-sm text-foreground placeholder:text-muted-foreground focus:border-foreground/40 focus:outline-none sm:flex-1"
          />
          <GlowButton type="submit">Scan</GlowButton>
        </form>

        <p className="mt-5 text-center">
          <Label>Read-only · no wallet connection · no signature</Label>
        </p>

        <div className="mx-auto mt-16 grid max-w-3xl grid-cols-2 gap-8 border-t border-border/60 pt-10 sm:grid-cols-3">
          {COLUMNS.map((col) => (
            <div key={col.title} className="flex flex-col gap-3">
              <Label>{col.title}</Label>
              <ul className="flex flex-col gap-2.5">
                {col.links.map((l) => (
                  <li key={l.label}>
                    <a
                      href={l.href}
                      className="label text-foreground transition-opacity hover:opacity-60"
                    >
                      {l.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mx-auto mt-10 flex max-w-3xl flex-col gap-4 border-t border-border/60 pt-8 sm:flex-row sm:items-center">
          <Label className="flex-1">© 2026 Approval Firewall</Label>
          <Label className="flex-1 text-muted-foreground sm:text-center">
            Built for the KeeperHub hackathon
          </Label>
          <nav aria-label="Social" className="flex flex-1 gap-4 sm:justify-end">
            <a
              href={REPO_URL}
              className="label text-foreground transition-opacity hover:opacity-60"
            >
              GitHub
            </a>
            <a
              href={MAINNET_TX_URL}
              className="label text-foreground transition-opacity hover:opacity-60"
            >
              BaseScan
            </a>
          </nav>
        </div>
      </Shell>
    </footer>
  )
}
