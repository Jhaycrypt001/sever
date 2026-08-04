import type { Metadata } from 'next'
import Link from 'next/link'
import { SiteFooter } from '@/components/firewall/site-footer'
import { SiteHeader } from '@/components/firewall/site-header'
import { Label, Shell } from '@/components/firewall/primitives'
import {
  ADR_COUNT,
  CHAIN_COUNT,
  MAINNET_TX_HASH,
  MAINNET_TX_URL,
  REPO_URL,
  SUPPORTED_CHAINS,
  TEST_COUNTS,
  truncateHash,
} from '@/lib/proof'

export const metadata: Metadata = {
  title: 'Docs · Approval Firewall',
  description:
    'How Approval Firewall decides what to revoke, what it covers, how to run it, and the limits it will tell you about up front.',
}

/**
 * A real documentation page at its own route, not an anchor on the landing
 * page. Written to be readable by someone deciding whether to trust this with
 * a wallet, which means the limits get their own section rather than a
 * footnote.
 *
 * Every number here comes from `lib/proof.ts`, the same source the marketing
 * page uses, so the two cannot drift apart.
 */

const SECTIONS = [
  { id: 'what', label: 'What it does' },
  { id: 'decide', label: 'How it decides' },
  { id: 'execute', label: 'How it revokes' },
  { id: 'coverage', label: 'Coverage and limits' },
  { id: 'run', label: 'Run it yourself' },
  { id: 'api', label: 'API' },
  { id: 'security', label: 'Security posture' },
]

function H({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2
      id={id}
      className="display scroll-mt-28 text-2xl text-foreground md:text-3xl"
    >
      {children}
    </h2>
  )
}

function P({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-pretty leading-relaxed text-muted-foreground">
      {children}
    </p>
  )
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <pre className="overflow-x-auto rounded border border-border/60 bg-card/40 p-4 font-mono text-[12px] leading-relaxed text-foreground">
      {children}
    </pre>
  )
}

export default function DocsPage() {
  return (
    <>
      <SiteHeader />
      <main className="bg-background pb-8 pt-28 md:pt-36">
        <Shell>
          <Label>§ Documentation</Label>
          <h1 className="display mt-6 max-w-[20ch] text-[2.5rem] leading-[0.95] text-foreground md:text-[3.5rem]">
            What it does, and what it will not do.
          </h1>
          <p className="mt-6 max-w-[62ch] text-pretty text-lg leading-relaxed text-muted-foreground">
            This page is written for someone deciding whether to point a wallet
            at this. The limits are near the top rather than buried, because
            they are the part that matters when you are deciding.
          </p>

          <div className="mt-16 grid grid-cols-1 gap-12 lg:grid-cols-[200px_minmax(0,1fr)] lg:gap-16">
            <nav
              aria-label="On this page"
              className="lg:sticky lg:top-28 lg:self-start"
            >
              <Label>On this page</Label>
              <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-2 lg:flex-col lg:gap-2.5">
                {SECTIONS.map((s) => (
                  <li key={s.id}>
                    <a
                      href={`#${s.id}`}
                      className="label text-foreground transition-opacity hover:opacity-60"
                    >
                      {s.label}
                    </a>
                  </li>
                ))}
              </ul>
            </nav>

            <div className="flex min-w-0 flex-col gap-16">
              {/* ------------------------------------------------ what */}
              <section className="flex flex-col gap-5">
                <H id="what">What it does</H>
                <P>
                  Every <code className="font-mono text-foreground">approve()</code>{' '}
                  you have ever signed stays live until something revokes it.
                  Most wallet drains are not a new exploit; they are an old
                  approval being called months later by a contract you forgot
                  you trusted.
                </P>
                <P>
                  Approval Firewall reads every outstanding ERC-20 approval for
                  an address, tiers each spender, and revokes the dangerous ones
                  as real onchain transactions. Reading is public and needs
                  nothing from you but the address — no wallet connection, no
                  signature, no seed phrase.
                </P>
              </section>

              {/* ---------------------------------------------- decide */}
              <section className="flex flex-col gap-5">
                <H id="decide">How it decides</H>
                <P>
                  The decision is made by an ordinary function over verified
                  provider signals, not by a language model. A model may write
                  the explanation sentence and, in agent mode, choose which
                  chain to look at next. It is not in the path that decides what
                  gets revoked, and the product runs correctly with no model key
                  at all.
                </P>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[34rem] border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-border/60 text-left">
                        <th className="py-3 pr-4 font-mono text-[11px] uppercase tracking-[0.08em] text-white/40">
                          Tier
                        </th>
                        <th className="py-3 pr-4 font-mono text-[11px] uppercase tracking-[0.08em] text-white/40">
                          Condition
                        </th>
                        <th className="py-3 font-mono text-[11px] uppercase tracking-[0.08em] text-white/40">
                          What happens
                        </th>
                      </tr>
                    </thead>
                    <tbody className="text-muted-foreground">
                      <tr className="border-b border-border/40">
                        <td className="py-3 pr-4 font-mono text-red-300">
                          DANGEROUS
                        </td>
                        <td className="py-3 pr-4">
                          flagged malicious, and not on the provider’s trust list
                        </td>
                        <td className="py-3 text-foreground">auto-revoked</td>
                      </tr>
                      <tr className="border-b border-border/40">
                        <td className="py-3 pr-4 font-mono text-amber-200">
                          WATCH
                        </td>
                        <td className="py-3 pr-4">
                          unverified contract, or contradictory signals
                        </td>
                        <td className="py-3">surfaced, never touched</td>
                      </tr>
                      <tr>
                        <td className="py-3 pr-4 font-mono text-white/60">
                          SAFE
                        </td>
                        <td className="py-3 pr-4">
                          verified, no malicious signal
                        </td>
                        <td className="py-3">left alone</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <P>
                  Contradictory signals never authorise a transaction. A spender
                  that is both trust-listed and flagged is a WATCH, not a
                  revoke — moving somebody’s funds on an ambiguous signal is
                  worse than leaving the decision to them.
                </P>
              </section>

              {/* --------------------------------------------- execute */}
              <section className="flex flex-col gap-5">
                <H id="execute">How it revokes</H>
                <P>
                  A revocation is <code className="font-mono text-foreground">approve(spender, 0)</code>{' '}
                  sent through KeeperHub, which relays it and pays the gas. The
                  protected wallet needs no balance and signs nothing at the
                  moment of execution.
                </P>
                <P>
                  Afterwards the allowance is read back off the chain. A status
                  field is the API’s opinion of its own work; the allowance is
                  the fact. The proof transaction on Base mainnet:
                </P>
                <Code>{`revoke   ${truncateHash(MAINNET_TX_HASH, 14, 10)}
eth_call allowance(wallet, spender) -> 0x0…0`}</Code>
                <p>
                  <a
                    href={MAINNET_TX_URL}
                    target="_blank"
                    rel="noreferrer"
                    className="label text-foreground underline-offset-4 transition-opacity hover:opacity-60"
                  >
                    View it on BaseScan →
                  </a>
                </p>
                <P>
                  Statuses are kept honest by design. A dry run reports{' '}
                  <code className="font-mono text-foreground">simulated</code>,
                  never <code className="font-mono text-foreground">revoked</code>
                  , because nothing reached the chain and the allowance is still
                  spendable. Anything that is not a confirmed transaction is
                  labelled as still live.
                </P>
              </section>

              {/* -------------------------------------------- coverage */}
              <section className="flex flex-col gap-5">
                <H id="coverage">Coverage and limits</H>
                <P>
                  Read this part before you trust the numbers on a scan.
                </P>
                <ul className="flex flex-col gap-3 text-muted-foreground">
                  <li className="flex flex-col gap-1">
                    <span className="text-foreground">
                      {CHAIN_COUNT} chains:{' '}
                      {SUPPORTED_CHAINS.map((c) => c.name).join(', ')}
                    </span>
                    <span className="text-sm">
                      That is the complete list the approval data source serves.
                      A wallet can hold approvals on networks outside it, and a
                      scan says which chains it covered so “0 dangerous” is never
                      mistaken for “clean everywhere”.
                    </span>
                  </li>
                  <li className="flex flex-col gap-1">
                    <span className="text-foreground">
                      It can scan any address, and revoke for exactly one.
                    </span>
                    <span className="text-sm">
                      <code className="font-mono">approve(spender, 0)</code>{' '}
                      clears the allowance of whoever sends it, so only the
                      wallet delegated to KeeperHub can be cleaned. Scanning
                      someone else’s wallet works and reports honestly;
                      revocation is refused and says why.
                    </span>
                  </li>
                  <li className="flex flex-col gap-1">
                    <span className="text-foreground">
                      A chain that cannot be reached is reported, not hidden.
                    </span>
                    <span className="text-sm">
                      One provider outage degrades that chain and the rest of the
                      scan still delivers. If no chain could be read the run
                      fails rather than reporting an empty, reassuring result.
                    </span>
                  </li>
                  <li className="flex flex-col gap-1">
                    <span className="text-foreground">
                      This is a hackathon build.
                    </span>
                    <span className="text-sm">
                      No billing, no uptime commitment, and no audit by a third
                      party. The decision log is public so you can judge it
                      yourself.
                    </span>
                  </li>
                </ul>
              </section>

              {/* -------------------------------------------------- run */}
              <section className="flex flex-col gap-5">
                <H id="run">Run it yourself</H>
                <P>
                  The whole stack is containerised, and it starts with no API
                  keys at all — the keyless mode uses deterministic providers so
                  nothing calls a paid service.
                </P>
                <Code>{`git clone ${REPO_URL}.git
cd ai-agent-boilerplate
cp .env.example .env

AGENT_PROVIDERS=fake docker compose --profile full up -d --build
open http://localhost:8080`}</Code>
                <P>
                  For live scanning, set{' '}
                  <code className="font-mono text-foreground">
                    AGENT_PROVIDERS=live
                  </code>{' '}
                  and add a KeeperHub key. Every command is in{' '}
                  <a
                    href={`${REPO_URL}/blob/main/docs/COMMANDS.md`}
                    target="_blank"
                    rel="noreferrer"
                    className="text-foreground underline underline-offset-4 transition-opacity hover:opacity-60"
                  >
                    docs/COMMANDS.md
                  </a>
                  .
                </P>
              </section>

              {/* -------------------------------------------------- api */}
              <section className="flex flex-col gap-5">
                <H id="api">API</H>
                <P>
                  The browser-facing API is documented as OpenAPI and served by
                  the running stack at{' '}
                  <code className="font-mono text-foreground">/api/docs</code>,
                  with the raw spec at{' '}
                  <code className="font-mono text-foreground">
                    /api/openapi.json
                  </code>
                  . The shape is pinned by cross-language contract fixtures, so
                  a frontend change cannot quietly redefine it.
                </P>
                <Code>{`POST /api/auth/register     create an account, get a mailed code
POST /api/auth/verify       answer the code — this is the sign-in
POST /api/searches          launch a scan   { wallet_address, mode }
GET  /api/searches/{id}     status, findings, decision journal
GET  /api/searches/{id}/events   live updates over SSE
POST /api/recurring         watch a wallet on a schedule`}</Code>
                <P>
                  Signing in takes two factors: a password and a code mailed to
                  the address. Answering the code is what issues the session —
                  a password alone never does.
                </P>
              </section>

              {/* --------------------------------------------- security */}
              <section className="flex flex-col gap-5">
                <H id="security">Security posture</H>
                <ul className="flex flex-col gap-3 text-muted-foreground">
                  <li>
                    <span className="text-foreground">No keys, ever.</span> The
                    product never holds a private key or a seed phrase.
                    Execution goes through KeeperHub’s delegated wallet.
                  </li>
                  <li>
                    <span className="text-foreground">
                      Fakes are refused in production.
                    </span>{' '}
                    Simulate-only and keyless-fake modes abort startup when the
                    environment says production, so a dry run cannot be
                    mistaken for protection.
                  </li>
                  <li>
                    <span className="text-foreground">
                      The worker never touches the database.
                    </span>{' '}
                    Results reach storage only through an authenticated callback
                    to the API.
                  </li>
                  <li>
                    <span className="text-foreground">
                      {TEST_COUNTS.python} Python, {TEST_COUNTS.rust} Rust,{' '}
                      {TEST_COUNTS.postgres} database and {TEST_COUNTS.browser}{' '}
                      browser tests,
                    </span>{' '}
                    and {ADR_COUNT} dated architecture decisions including the
                    ones that were wrong and revisited.
                  </li>
                </ul>
                <p className="flex flex-wrap gap-x-6 gap-y-2 pt-2">
                  <a
                    href={`${REPO_URL}/blob/main/docs/ARCHITECTURE.md`}
                    target="_blank"
                    rel="noreferrer"
                    className="label text-foreground underline-offset-4 transition-opacity hover:opacity-60"
                  >
                    Read the decision log →
                  </a>
                  <Link
                    href="/console"
                    className="label text-foreground underline-offset-4 transition-opacity hover:opacity-60"
                  >
                    Open the console →
                  </Link>
                </p>
              </section>
            </div>
          </div>
        </Shell>
      </main>
      <SiteFooter />
    </>
  )
}
