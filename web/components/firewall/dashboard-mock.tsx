import { truncateHash } from '@/lib/proof'
import { cn } from '@/lib/utils'

const NAV = [
  { icon: '◫', label: 'Overview', active: true },
  { icon: '⊞', label: 'Wallets' },
  { icon: '⊡', label: 'Findings' },
  { icon: '◇', label: 'Revocations' },
  { icon: '⚙', label: 'Settings' },
]

const TABS = ['OVERVIEW', 'FINDINGS', 'REVOCATIONS', 'JOURNAL', 'SETTINGS']

const KPIS = [
  { label: 'WALLETS WATCHED', value: '128', delta: '+14 this week' },
  { label: 'DANGEROUS APPROVALS', value: '37', delta: 'across 5 chains' },
  { label: 'AUTO-REVOKED', value: '31', delta: '6 left for review' },
  { label: 'TIME TO REVOKE', value: '7.9s', delta: 'scan to confirmation' },
]

const WEEKS = [
  { m: 'W1', v: 34 },
  { m: 'W2', v: 52 },
  { m: 'W3', v: 28 },
  { m: 'W4', v: 66 },
  { m: 'W5', v: 45 },
  { m: 'W6', v: 81 },
  { m: 'W7', v: 58 },
  { m: 'W8', v: 92 },
  { m: 'W9', v: 49 },
  { m: 'W10', v: 74 },
  { m: 'W11', v: 61 },
  { m: 'W12', v: 86 },
]

const REVOKED = [
  { i: 'PH', n: 'Phishing proxy', e: '0xbad0…ad00 · USDC', a: 'revoked' },
  { i: 'DR', n: 'Drainer router', e: '0x9f2c…41ab · WETH', a: 'revoked' },
  { i: 'UN', n: 'Unverified spender', e: '0xca11…a110 · DAI', a: 'watch' },
  { i: 'HP', n: 'Honeypot token', e: '0x7e31…09fd · COW', a: 'revoked' },
  { i: 'SC', n: 'Scam airdrop', e: '0x4d88…c2e1 · XYZ', a: 'revoked' },
]

const CHAINS = [
  { r: 'Ethereum', p: 44 },
  { r: 'Base', p: 21 },
  { r: 'Arbitrum', p: 15 },
  { r: 'Polygon', p: 11 },
  { r: 'Sepolia', p: 9 },
]

const SPENDERS = [
  ['0xbad000…0bad00', 'USDC', 'Unlimited', 'DANGEROUS'],
  ['0x9f2c41…ab7e12', 'WETH', 'Unlimited', 'DANGEROUS'],
  ['0xca1100…0ca110', 'DAI', '1,000', 'WATCH'],
  ['0x5afe00…005afe', 'USDT', '250', 'SAFE'],
  ['0x1f0ba2…88c4d0', 'ARB', 'Unlimited', 'WATCH'],
]

const LEDGER = [
  [
    truncateHash(
      '0xa3e2b054752adda3aa9696a6d5460ac40c9670e34044da276b62ee10d9822c28',
    ),
    'WETH',
    'Sepolia',
    'revoked',
    '75,255',
    '03:02:03',
  ],
  ['0x7c41d9…e0b2', 'USDC', 'Ethereum', 'revoked', '46,118', '02:54:11'],
  ['0x2be8a1…4f7c', 'DAI', 'Base', 'pending', '—', '02:51:47'],
  ['0x93fd0c…7a19', 'COW', 'Ethereum', 'revoked', '44,902', '02:47:26'],
  ['0x51ac77…bb03', 'XYZ', 'Polygon', 'failed', '—', '02:41:58'],
  ['0xe0177b…9d44', 'WETH', 'Arbitrum', 'revoked', '48,301', '02:38:12'],
  ['0x6a2c19…30fe', 'USDT', 'Base', 'simulated', '—', '02:33:05'],
  ['0xb84f30…1c77', 'ARB', 'Arbitrum', 'revoked', '45,760', '02:29:44'],
]

const JOURNAL = [
  { i: '01', t: 'scan · chain 1 — 12 approvals, 3 new', w: '2m ago' },
  { i: '02', t: 'assess · 1 dangerous, 2 watch', w: '2m ago' },
  { i: '03', t: 'revoke · 0xbad0…ad00 submitted', w: '2m ago' },
  { i: '04', t: 'revoke · confirmed, tx 0xa3e2…22c28', w: '1m ago' },
  { i: '05', t: 'scan · chain 8453 — 0 new', w: '1m ago' },
  { i: '06', t: 'finish · all configured chains scanned', w: '58s ago' },
]

const SPARK =
  'M0 34 L14 26 L28 30 L42 16 L56 22 L70 10 L84 18 L98 6 L112 14 L126 4 L140 12 L154 2'

function Panel({
  className,
  children,
}: {
  className?: string
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        'rounded-md border border-white/[0.07] bg-white/[0.015] p-4',
        className,
      )}
    >
      {children}
    </div>
  )
}

function Cap({ children }: { children: React.ReactNode }) {
  return (
    <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-white/40">
      {children}
    </span>
  )
}

function Avatar({ children }: { children: React.ReactNode }) {
  return (
    <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-white/[0.08] font-mono text-[8px] text-white/60">
      {children}
    </span>
  )
}

const TIER_STYLE: Record<string, string> = {
  DANGEROUS: 'bg-red-500/15 text-red-300/80',
  WATCH: 'bg-amber-400/12 text-amber-200/75',
  SAFE: 'bg-white/[0.05] text-white/45',
}

const STATUS_STYLE: Record<string, string> = {
  revoked: 'bg-white/10 text-white/75',
  pending: 'bg-white/[0.05] text-white/45',
  simulated: 'bg-white/[0.05] text-white/45',
  failed: 'bg-red-500/15 text-red-300/80',
}

export function DashboardMock() {
  return (
    <div
      aria-hidden="true"
      className="w-[1440px] select-none overflow-hidden rounded-xl border border-white/10 bg-[#0c0c0c] text-white shadow-2xl"
    >
      {/* window chrome */}
      <div className="flex items-center gap-3 border-b border-white/[0.07] px-4 py-2.5">
        <div className="flex gap-1.5">
          <span className="size-2 rounded-full bg-white/15" />
          <span className="size-2 rounded-full bg-white/15" />
          <span className="size-2 rounded-full bg-white/15" />
        </div>
        <span className="ml-2 font-mono text-[10px] text-white/40">
          approval-firewall · console
        </span>
        <span className="ml-auto rounded border border-white/10 px-1.5 py-0.5 font-mono text-[9px] text-white/40">
          ⌘K
        </span>
      </div>

      <div className="flex">
        {/* sidebar */}
        <aside className="flex w-[190px] shrink-0 flex-col gap-5 border-r border-white/[0.07] p-4">
          <div className="flex items-center gap-2">
            <span className="flex size-6 items-center justify-center rounded bg-white/[0.08]">
              <svg
                viewBox="0 0 32 32"
                className="size-3.5 text-white/70"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                role="img"
                aria-label="Logo"
              >
                <path d="M16 2.5 4.5 7v9c0 6.6 4.8 11.6 11.5 13.5C22.7 27.6 27.5 22.6 27.5 16V7L16 2.5Z" />
                <path d="M13.4 13 11.8 14.6a3.4 3.4 0 0 0 4.8 4.8l1.6-1.6" />
                <path d="M18.6 19 20.2 17.4a3.4 3.4 0 0 0-4.8-4.8l-1.6 1.6" />
              </svg>
            </span>
            <span className="text-xs font-medium text-white/80">Firewall</span>
          </div>

          <nav className="flex flex-col gap-0.5">
            {NAV.map((n) => (
              <button
                key={n.label}
                type="button"
                className={cn(
                  'flex items-center gap-2.5 rounded px-2.5 py-2 text-left text-xs',
                  n.active
                    ? 'bg-white/[0.07] text-white'
                    : 'text-white/45 hover:text-white/70',
                )}
              >
                <span className="text-[11px] opacity-70">{n.icon}</span>
                <span>{n.label}</span>
              </button>
            ))}
          </nav>

          <div className="mt-auto rounded border border-white/[0.07] p-2.5">
            <Cap>Executor</Cap>
            <p className="mt-1 text-[11px] text-white/70">
              KeeperHub <span className="text-white/35">· gas sponsored</span>
            </p>
          </div>
        </aside>

        {/* main */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-6 border-b border-white/[0.07] px-5">
            {TABS.map((t, idx) => (
              <button
                key={t}
                type="button"
                className={cn(
                  'border-b py-3 font-mono text-[10px] tracking-[0.1em]',
                  idx === 0
                    ? 'border-white/70 text-white'
                    : 'border-transparent text-white/35',
                )}
              >
                {t}
              </button>
            ))}
          </div>

          <div className="flex flex-col gap-3 p-5">
            {/* KPI row */}
            <div className="grid grid-cols-4 gap-3">
              {KPIS.map((k) => (
                <Panel key={k.label}>
                  <Cap>{k.label}</Cap>
                  <p className="display mt-2 text-2xl">{k.value}</p>
                  <p className="mt-1.5 font-mono text-[9px] text-white/35">
                    {k.delta}
                  </p>
                </Panel>
              ))}
            </div>

            {/* chart + recent revocations */}
            <div className="grid grid-cols-[1.55fr_1fr] gap-3">
              <Panel>
                <div className="flex items-start justify-between">
                  <div>
                    <Cap>Revocations · 12 weeks</Cap>
                    <p className="display mt-2 text-2xl">726</p>
                    <p className="mt-1 font-mono text-[9px] text-white/35">
                      all executed via KeeperHub
                    </p>
                  </div>
                  <div className="flex gap-1">
                    <span className="rounded border border-white/15 px-2 py-1 font-mono text-[9px] text-white/70">
                      WEEKLY
                    </span>
                    <span className="rounded border border-transparent px-2 py-1 font-mono text-[9px] text-white/30">
                      DAILY
                    </span>
                  </div>
                </div>
                <div className="mt-5 flex h-[132px] items-end gap-2">
                  {WEEKS.map((wk) => (
                    <div
                      key={wk.m}
                      className="flex flex-1 flex-col items-center gap-2"
                    >
                      <div
                        className="w-full rounded-sm bg-white/[0.16]"
                        style={{ height: `${wk.v}%` }}
                      />
                      <span className="font-mono text-[8px] text-white/30">
                        {wk.m}
                      </span>
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel>
                <div className="flex items-center justify-between">
                  <Cap>Recent revocations</Cap>
                  <span className="font-mono text-[9px] text-white/35">
                    5 today
                  </span>
                </div>
                <ul className="mt-4 flex flex-col gap-3">
                  {REVOKED.map((s) => (
                    <li key={s.i} className="flex items-center gap-2.5">
                      <Avatar>{s.i}</Avatar>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[11px] text-white/80">
                          {s.n}
                        </span>
                        <span className="block truncate font-mono text-[8px] text-white/30">
                          {s.e}
                        </span>
                      </span>
                      <span className="font-mono text-[10px] text-white/70">
                        {s.a}
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            </div>

            {/* scan volume + chains */}
            <div className="grid grid-cols-[1.55fr_1fr] gap-3">
              <Panel>
                <div className="flex items-start justify-between">
                  <div>
                    <Cap>Approvals scanned · last 24h</Cap>
                    <p className="display mt-2 text-2xl">
                      3.1K{' '}
                      <span className="font-mono text-[10px] font-normal text-white/35">
                        approvals
                      </span>
                    </p>
                  </div>
                  <div className="flex gap-1">
                    {['24H', '7D', '30D'].map((t, i) => (
                      <span
                        key={t}
                        className={cn(
                          'rounded px-2 py-1 font-mono text-[9px]',
                          i === 0
                            ? 'border border-white/15 text-white/70'
                            : 'text-white/30',
                        )}
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
                <svg
                  viewBox="0 0 154 40"
                  preserveAspectRatio="none"
                  className="mt-4 h-16 w-full text-white/45"
                  fill="none"
                >
                  <path
                    d={SPARK}
                    stroke="currentColor"
                    strokeWidth="1"
                    vectorEffect="non-scaling-stroke"
                  />
                </svg>
              </Panel>

              <Panel>
                <Cap>Findings by chain</Cap>
                <ul className="mt-4 flex flex-col gap-2.5">
                  {CHAINS.map((r) => (
                    <li key={r.r} className="flex items-center gap-3">
                      <span className="w-[92px] shrink-0 truncate text-[10px] text-white/65">
                        {r.r}
                      </span>
                      <span className="h-1 flex-1 rounded-full bg-white/[0.07]">
                        <span
                          className="block h-full rounded-full bg-white/25"
                          style={{ width: `${(r.p / 44) * 100}%` }}
                        />
                      </span>
                      <span className="w-7 shrink-0 text-right font-mono text-[9px] text-white/40">
                        {r.p}%
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>
            </div>

            {/* outstanding approvals */}
            <Panel>
              <div className="flex items-center justify-between">
                <Cap>Outstanding approvals</Cap>
                <Cap>Sorted by risk</Cap>
              </div>
              <table className="mt-4 w-full text-left">
                <thead>
                  <tr className="border-b border-white/[0.07]">
                    {['SPENDER', 'TOKEN', 'ALLOWANCE', 'TIER'].map((h, i) => (
                      <th
                        key={h}
                        className={cn(
                          'pb-2 font-mono text-[9px] font-normal tracking-[0.1em] text-white/35',
                          i > 0 && 'text-right',
                        )}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {SPENDERS.map((row) => (
                    <tr key={row[0]} className="border-b border-white/[0.04]">
                      {row.map((cell, i) => (
                        <td
                          key={cell}
                          className={cn(
                            'py-2 font-mono text-[10px]',
                            i === 0
                              ? 'text-white/70'
                              : 'text-right text-white/45',
                          )}
                        >
                          {i === 3 ? (
                            <span
                              className={cn(
                                'rounded px-1.5 py-0.5 text-[9px]',
                                TIER_STYLE[cell],
                              )}
                            >
                              {cell}
                            </span>
                          ) : (
                            cell
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>

            {/* revocation ledger */}
            <Panel>
              <div className="flex items-center justify-between">
                <Cap>Revocation ledger</Cap>
                <span className="font-mono text-[9px] text-white/35">
                  8 records
                </span>
              </div>
              <table className="mt-4 w-full text-left">
                <thead>
                  <tr className="border-b border-white/[0.07]">
                    {[
                      'TX HASH',
                      'TOKEN',
                      'CHAIN',
                      'STATUS',
                      'GAS USED',
                      'TIME',
                    ].map((h, i) => (
                      <th
                        key={h}
                        className={cn(
                          'pb-2 font-mono text-[9px] font-normal tracking-[0.1em] text-white/35',
                          i > 3 && 'text-right',
                        )}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {LEDGER.map((row) => (
                    <tr key={row[0]} className="border-b border-white/[0.04]">
                      {row.map((cell, i) => (
                        <td
                          key={`${row[0]}-${i}`}
                          className={cn(
                            'py-2 font-mono text-[10px]',
                            i > 3 ? 'text-right' : '',
                            i === 0 ? 'text-white/70' : 'text-white/45',
                          )}
                        >
                          {i === 3 ? (
                            <span
                              className={cn(
                                'rounded px-1.5 py-0.5 text-[9px]',
                                STATUS_STYLE[cell],
                              )}
                            >
                              {cell}
                            </span>
                          ) : (
                            cell
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>

            {/* journal + rate + coverage */}
            <div className="grid grid-cols-3 gap-3">
              <Panel>
                <Cap>Agent journal</Cap>
                <ul className="mt-4 flex flex-col gap-3">
                  {JOURNAL.map((f) => (
                    <li key={f.i} className="flex items-center gap-2.5">
                      <Avatar>{f.i}</Avatar>
                      <span className="min-w-0 flex-1 truncate text-[10px] text-white/65">
                        {f.t}
                      </span>
                      <span className="shrink-0 font-mono text-[8px] text-white/30">
                        {f.w}
                      </span>
                    </li>
                  ))}
                </ul>
              </Panel>

              <Panel>
                <Cap>Auto-revoke rate</Cap>
                <p className="display mt-2 text-2xl">83.8%</p>
                <p className="mt-1 font-mono text-[9px] text-white/35">
                  of dangerous findings
                </p>
                <div className="mt-5 flex h-[92px] items-end gap-2">
                  {[62, 78, 44, 90, 71, 36, 55].map((h, i) => (
                    <div
                      key={`${h}-${i}`}
                      className="flex flex-1 flex-col items-center gap-2"
                    >
                      <div
                        className="w-full rounded-sm bg-white/[0.16]"
                        style={{ height: `${h}%` }}
                      />
                      <span className="font-mono text-[8px] text-white/30">
                        {['M', 'T', 'W', 'T', 'F', 'S', 'S'][i]}
                      </span>
                    </div>
                  ))}
                </div>
              </Panel>

              <Panel>
                <Cap>Scan coverage</Cap>
                <p className="display mt-2 text-2xl">
                  99.94%{' '}
                  <span className="font-mono text-[10px] font-normal text-white/35">
                    last 90 days
                  </span>
                </p>
                <div className="mt-5 grid grid-cols-15 gap-[3px]">
                  {Array.from({ length: 90 }).map((_, i) => (
                    <span
                      key={i}
                      className={cn(
                        'aspect-square rounded-[1px]',
                        i % 23 === 7
                          ? 'bg-white/30'
                          : i % 11 === 3
                            ? 'bg-white/[0.14]'
                            : 'bg-white/[0.07]',
                      )}
                    />
                  ))}
                </div>
                <div className="mt-3 flex justify-between">
                  <Cap>90d ago</Cap>
                  <Cap>Today</Cap>
                </div>
              </Panel>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
