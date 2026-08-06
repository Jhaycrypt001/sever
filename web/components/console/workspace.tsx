'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import {
  ApiError,
  api,
  type JobMode,
  type JobStatus,
  type RecurringScan,
  type ScanJob,
  type ScanJobDetail,
} from '@/lib/api'
import { chainName, truncateAddress } from '@/lib/chains'
import { cn } from '@/lib/utils'
import { FindingsTable } from './findings-table'
import { Journal } from './journal'
import { JobStatusBadge } from './status'

type Call = <T>(fn: (token: string) => Promise<T>) => Promise<T>

const ADDRESS = /^0x[a-fA-F0-9]{40}$/

/** How often a running job is re-fetched while its SSE stream is open. */
const POLL_INTERVAL_MS = 1500

function isTerminal(status: JobStatus): boolean {
  return status === 'completed' || status === 'failed'
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function message(e: unknown): string {
  return e instanceof ApiError ? e.message : 'Could not reach the API.'
}

export function Workspace({
  call,
  token,
  initialAddress,
  onSignOut,
}: {
  call: Call
  token: string
  initialAddress: string
  onSignOut: () => void
}) {
  const [jobs, setJobs] = useState<ScanJob[]>([])
  const [recurring, setRecurring] = useState<RecurringScan[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ScanJobDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refreshLists = useCallback(async () => {
    try {
      const [nextJobs, nextRecurring] = await Promise.all([
        call((t) => api.listScans(t)),
        call((t) => api.listRecurring(t)),
      ])
      setJobs(nextJobs)
      setRecurring(nextRecurring)
      setSelectedId((current) => current ?? nextJobs[0]?.id ?? null)
    } catch (e) {
      setError(message(e))
    }
  }, [call])

  useEffect(() => {
    void refreshLists()
  }, [refreshLists])

  // Live job detail (ADR-026). Polling is unconditional and SSE runs alongside
  // it, rather than polling only when the stream errors.
  //
  // A buffering proxy does not fail — it accepts the connection, returns 200,
  // and then delivers nothing. An error-triggered fallback never fires, so the
  // view silently freezes on whatever the first fetch returned. That is how a
  // scan sat at "Queued" for its entire run while the job had actually paused
  // for input. Treating silence as failure is the only version that holds, so
  // the poll is the correctness guarantee and SSE is the latency win on top.
  useEffect(() => {
    if (!selectedId) return
    let cancelled = false
    let finished = false
    const controller = new AbortController()

    const apply = (next: ScanJobDetail) => {
      if (cancelled) return
      setDetail(next)
      // A later success clears an earlier transport error; otherwise one
      // dropped request leaves a red banner up for the rest of the session.
      setError(null)
      if (isTerminal(next.status)) {
        finished = true
        controller.abort()
        void refreshLists()
      }
    }

    const poll = async () => {
      while (!cancelled && !finished) {
        try {
          apply(await call((t) => api.getScan(selectedId, t)))
        } catch (e) {
          if (!cancelled) setError(message(e))
          return
        }
        if (cancelled || finished) return
        await sleep(POLL_INTERVAL_MS)
      }
    }

    const stream = async () => {
      try {
        await api.streamScan(selectedId, token, apply, controller.signal)
      } catch {
        // Never fatal: the poll above is what guarantees the view converges.
      }
    }

    void poll()
    void stream()

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [selectedId, token, call, refreshLists])

  const onLaunched = useCallback(
    (jobId: string) => {
      setSelectedId(jobId)
      setDetail(null)
      void refreshLists()
    },
    [refreshLists],
  )

  return (
    <div className="flex flex-col gap-6">
      <Launcher
        call={call}
        initialAddress={initialAddress}
        onLaunched={onLaunched}
        onSignOut={onSignOut}
      />

      {error ? (
        <p
          role="alert"
          className="rounded border border-red-500/25 bg-red-500/10 px-4 py-2.5 font-mono text-[11px] text-red-300"
        >
          {error}
        </p>
      ) : null}

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <div className="flex flex-col gap-4">
          <ScanList
            jobs={jobs}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          <RecurringList
            call={call}
            recurring={recurring}
            onChanged={refreshLists}
          />
        </div>

        <ScanDetail call={call} detail={detail} selectedId={selectedId} />
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ launcher */

function Launcher({
  call,
  initialAddress,
  onLaunched,
  onSignOut,
}: {
  call: Call
  initialAddress: string
  onLaunched: (jobId: string) => void
  onSignOut: () => void
}) {
  const [address, setAddress] = useState(initialAddress)
  const [mode, setMode] = useState<JobMode>('workflow')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const valid = ADDRESS.test(address.trim())

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!valid) return
    setBusy(true)
    setError(null)
    try {
      const { job_id } = await call((t) =>
        api.launchScan(address.trim(), t, mode),
      )
      onLaunched(job_id)
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-lg border border-white/[0.08] bg-white/[0.015] p-4 md:flex-row md:items-end">
      <form onSubmit={submit} className="flex min-w-0 flex-1 flex-col gap-3 md:flex-row md:items-end">
        <label htmlFor="wallet" className="flex min-w-0 flex-1 flex-col gap-1.5">
          <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40">
            Wallet to scan
          </span>
          <input
            id="wallet"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder="0x…"
            spellCheck={false}
            autoComplete="off"
            className="h-11 min-w-0 rounded border border-white/[0.12] bg-black/40 px-3 font-mono text-sm text-white placeholder:text-white/25 focus:border-white/40 focus:outline-none"
          />
        </label>

        <label htmlFor="mode" className="flex flex-col gap-1.5">
          <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40">
            Mode
          </span>
          {/* The difference that matters is not coverage, it is whether the
              run is allowed to write to the chain: workflow mode is read-only
              by design (ADR-030/058) and agent mode auto-revokes. Naming them
              after the internals would hide exactly the thing a user must
              understand before clicking. */}
          <select
            id="mode"
            value={mode}
            onChange={(e) => setMode(e.target.value as JobMode)}
            className="h-11 rounded border border-white/[0.12] bg-black/40 px-3 font-mono text-sm text-white focus:border-white/40 focus:outline-none"
          >
            <option value="workflow">Report only · never revokes</option>
            <option value="agent">Auto-revoke the dangerous ones</option>
          </select>
        </label>

        <button
          type="submit"
          disabled={busy || !valid}
          className="h-11 shrink-0 rounded-full bg-white px-6 text-sm font-medium text-black transition-opacity hover:opacity-85 disabled:opacity-40"
        >
          {busy ? 'Launching…' : 'Scan'}
        </button>
      </form>

      <div className="flex shrink-0 items-center gap-4">
        <Link
          href="/"
          className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40 transition-colors hover:text-white/70"
        >
          ← Home
        </Link>
        <button
          type="button"
          onClick={onSignOut}
          className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40 transition-colors hover:text-white/70"
        >
          Sign out
        </button>
      </div>

      {error ? (
        <p role="alert" className="font-mono text-[11px] text-red-300">
          {error}
        </p>
      ) : null}
    </div>
  )
}

/* ----------------------------------------------------------------- scan list */

/**
 * Mirrors `SearchQueries::HISTORY_LIMIT` on the backend (ADR-075).
 *
 * Only used to decide whether the count is showing everything or a capped
 * window — the cap itself is the server's, and this never asks for more.
 */
const HISTORY_LIMIT = 50

function ScanList({
  jobs,
  selectedId,
  onSelect,
}: {
  jobs: ScanJob[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  return (
    <section className="rounded-lg border border-white/[0.08] bg-white/[0.015]">
      {/*
        The count says "50+" at the cap rather than a flat 50, because the
        server sends the most recent 50 (ADR-075) and a bare "50" would read
        as "you have run 50 scans" to someone who has run three hundred.
      */}
      <header className="border-b border-white/[0.06] px-4 py-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/40">
          Scans · {jobs.length}
          {jobs.length >= HISTORY_LIMIT ? '+ · most recent' : ''}
        </span>
      </header>
      {jobs.length === 0 ? (
        <p className="px-4 py-8 text-center font-mono text-xs text-white/35">
          No scans yet.
        </p>
      ) : (
        <ul className="max-h-[420px] divide-y divide-white/[0.06] overflow-y-auto">
          {jobs.map((job) => (
            <li key={job.id}>
              <button
                type="button"
                onClick={() => onSelect(job.id)}
                className={cn(
                  'flex w-full flex-col gap-1.5 px-4 py-3 text-left transition-colors hover:bg-white/[0.03]',
                  job.id === selectedId && 'bg-white/[0.05]',
                )}
              >
                <span className="font-mono text-xs text-white/75">
                  {truncateAddress(job.wallet_address, 8, 6)}
                </span>
                <span className="flex items-center gap-2">
                  <JobStatusBadge status={job.status} />
                  {job.recurring_search_id ? (
                    <span className="font-mono text-[9px] uppercase tracking-[0.08em] text-white/30">
                      recurring
                    </span>
                  ) : null}
                </span>
                <span className="font-mono text-[9px] text-white/25">
                  {new Date(job.created_at).toLocaleString()}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/* ------------------------------------------------------------ recurring list */

function RecurringList({
  call,
  recurring,
  onChanged,
}: {
  call: Call
  recurring: RecurringScan[]
  onChanged: () => void
}) {
  const [address, setAddress] = useState('')
  const [intervalMinutes, setIntervalMinutes] = useState('60')
  const [webhookUrl, setWebhookUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const remove = async (id: string) => {
    await call((t) => api.deleteRecurring(id, t)).catch(() => {})
    onChanged()
  }

  async function watch(event: React.FormEvent) {
    event.preventDefault()
    const wallet = address.trim()
    if (!ADDRESS.test(wallet)) return
    setBusy(true)
    setError(null)
    try {
      await call((t) =>
        api.createRecurring(
          wallet,
          'workflow',
          Number(intervalMinutes),
          t,
          webhookUrl.trim() || undefined,
        ),
      )
      setAddress('')
      setWebhookUrl('')
      onChanged()
    } catch (e) {
      setError(message(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section
      data-testid="recurring-section"
      className="rounded-lg border border-white/[0.08] bg-white/[0.015]"
    >
      <header className="border-b border-white/[0.06] px-4 py-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/40">
          Watched wallets · {recurring.length}
        </span>
      </header>

      <form onSubmit={watch} className="flex flex-col gap-2 border-b border-white/[0.06] p-4">
        <label htmlFor="watch-address" className="sr-only">
          Wallet to watch
        </label>
        <input
          id="watch-address"
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder="Wallet to watch"
          spellCheck={false}
          autoComplete="off"
          className="h-9 rounded border border-white/[0.12] bg-black/40 px-2.5 font-mono text-xs text-white placeholder:text-white/25 focus:border-white/40 focus:outline-none"
        />
        <div className="flex gap-2">
          <label htmlFor="watch-interval" className="sr-only">
            Interval in minutes
          </label>
          <input
            id="watch-interval"
            type="number"
            min={1}
            value={intervalMinutes}
            onChange={(e) => setIntervalMinutes(e.target.value)}
            className="h-9 w-20 rounded border border-white/[0.12] bg-black/40 px-2.5 font-mono text-xs text-white focus:border-white/40 focus:outline-none"
          />
          <label htmlFor="watch-webhook" className="sr-only">
            Digest webhook URL
          </label>
          <input
            id="watch-webhook"
            value={webhookUrl}
            onChange={(e) => setWebhookUrl(e.target.value)}
            placeholder="Digest webhook (optional)"
            spellCheck={false}
            className="h-9 min-w-0 flex-1 rounded border border-white/[0.12] bg-black/40 px-2.5 font-mono text-xs text-white placeholder:text-white/25 focus:border-white/40 focus:outline-none"
          />
        </div>
        <button
          type="submit"
          disabled={busy || !ADDRESS.test(address.trim())}
          className="h-9 rounded-full bg-white/90 text-xs font-medium text-black transition-opacity hover:opacity-85 disabled:opacity-30"
        >
          {busy ? 'Saving…' : 'Watch'}
        </button>
        {error ? (
          <p role="alert" className="font-mono text-[10px] text-red-300">
            {error}
          </p>
        ) : null}
      </form>

      {recurring.length === 0 ? (
        <p className="px-4 py-6 text-center font-mono text-[11px] leading-relaxed text-white/35">
          Nothing watched yet. A watched wallet is re-scanned on a schedule and
          only alerts on approvals it has not seen before.
        </p>
      ) : (
        <ul className="divide-y divide-white/[0.06]">
          {recurring.map((r) => (
            <li key={r.id} className="flex items-center gap-3 px-4 py-3">
              <span className="min-w-0 flex-1">
                <span className="block font-mono text-xs text-white/75">
                  {truncateAddress(r.wallet_address, 8, 6)}
                </span>
                <span className="block font-mono text-[9px] text-white/30">
                  every {r.interval_minutes} min
                  {r.webhook_url ? ' · digest on' : ''}
                </span>
              </span>
              <button
                type="button"
                onClick={() => void remove(r.id)}
                aria-label={`Stop watching ${r.wallet_address}`}
                className="shrink-0 font-mono text-[10px] uppercase tracking-[0.08em] text-white/30 transition-colors hover:text-red-300"
              >
                Stop
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/* --------------------------------------------------------------- scan detail */

function ScanDetail({
  call,
  detail,
  selectedId,
}: {
  call: Call
  detail: ScanJobDetail | null
  selectedId: string | null
}) {
  const [tab, setTab] = useState<'findings' | 'journal'>('findings')

  const counts = useMemo(() => {
    const results = detail?.results ?? []
    return {
      dangerous: results.filter((r) => r.tier === 'dangerous').length,
      watch: results.filter((r) => r.tier === 'watch').length,
      safe: results.filter((r) => r.tier === 'safe').length,
      revoked: results.filter((r) => r.revocation_status === 'revoked').length,
    }
  }, [detail])

  /**
   * Chains the run could not reach (ADR-064). Read off the journal rather than
   * a dedicated field: `degraded` steps are already the record of what was
   * skipped, and a second source of truth for the same fact would be one more
   * thing that can drift.
   */
  const unscannedChains = useMemo(() => {
    const chains = (detail?.steps ?? [])
      .filter((s) => s.kind === 'degraded' && s.detail)
      .map((s) => s.detail)
    return [...new Set(chains)]
  }, [detail])

  /**
   * The chains this run actually covered (ADR-067).
   *
   * Stated because the counts above are only true *of these chains*. A wallet
   * can hold approvals on any EVM network; the approval source covers three.
   * Without this line "0 dangerous" reads as "you are safe", when it means
   * "safe on the three we looked at" — the same shape of omission ADR-064
   * fixed for chains that failed, applied to chains never attempted.
   */
  const scannedChains = useMemo(() => {
    const chains = (detail?.steps ?? [])
      .filter((s) => s.kind === 'scan' && s.detail)
      .map((s) => s.detail)
    return [...new Set(chains)]
  }, [detail])

  if (!selectedId) {
    return (
      <section className="rounded-lg border border-white/[0.08] bg-white/[0.015] px-6 py-20 text-center">
        <p className="font-mono text-xs text-white/35">
          Paste a wallet address above to run the first scan.
        </p>
      </section>
    )
  }

  if (!detail) {
    return (
      <section className="rounded-lg border border-white/[0.08] bg-white/[0.015] px-6 py-20 text-center">
        <p className="font-mono text-xs text-white/35">Loading scan…</p>
      </section>
    )
  }

  return (
    <section
      data-testid="scan-detail"
      className="flex flex-col rounded-lg border border-white/[0.08] bg-white/[0.015]"
    >
      <header className="flex flex-col gap-4 border-b border-white/[0.06] p-4">
        <div
          data-testid="run-status"
          data-status={detail.status}
          className="flex flex-wrap items-center gap-3"
        >
          <JobStatusBadge status={detail.status} />
          <span className="break-all font-mono text-sm text-white/85">
            {detail.wallet_address}
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/30">
            {detail.mode}
          </span>
        </div>

        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Dangerous" value={counts.dangerous} tone="danger" />
          <Stat label="Watch" value={counts.watch} tone="warn" />
          <Stat label="Safe" value={counts.safe} />
          <Stat label="Revoked onchain" value={counts.revoked} tone="good" />
        </dl>

        {detail.mode === 'workflow' ? (
          <p
            data-testid="report-only-notice"
            className="rounded border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 font-mono text-[10px] uppercase tracking-[0.08em] text-amber-200/90"
          >
            Report-only run · nothing was revoked · every allowance below is
            still live
          </p>
        ) : null}

        {/*
          Partial coverage (ADR-064). Stated at the top of the run, not left in
          the journal, because the dangerous reading of this page is "0
          dangerous approvals" — and that number means nothing for a chain
          nobody could reach. Someone who never opens the journal still has to
          learn the sweep was incomplete.
        */}
        {unscannedChains.length > 0 ? (
          <p
            data-testid="degraded-notice"
            className="rounded border border-red-500/25 bg-red-500/[0.08] px-3 py-2 font-mono text-[10px] uppercase tracking-[0.08em] text-red-300"
          >
            Incomplete scan · {unscannedChains.map(chainName).join(' · ')} could
            not be reached · counts below do not cover{' '}
            {unscannedChains.length > 1 ? 'those chains' : 'that chain'}
          </p>
        ) : null}

        {scannedChains.length > 0 ? (
          <p
            data-testid="coverage-notice"
            className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/35"
          >
            Covered {scannedChains.map(chainName).join(' · ')} · approvals on
            other networks were not examined
          </p>
        ) : null}

        {detail.usage.cost_usd > 0 ? (
          <p className="font-mono text-[10px] text-white/30">
            Run cost ${detail.usage.cost_usd.toFixed(4)} ·{' '}
            {detail.usage.llm_calls} model calls · gas sponsored by KeeperHub
          </p>
        ) : (
          <p className="font-mono text-[10px] text-white/30">
            No model calls on this run · gas sponsored by KeeperHub
          </p>
        )}

        {detail.error ? (
          <p
            role="alert"
            className="rounded border border-red-500/25 bg-red-500/10 px-3 py-2 font-mono text-[11px] text-red-300"
          >
            {detail.error}
          </p>
        ) : null}

        {detail.status === 'awaiting_input' && detail.question ? (
          <AnswerForm call={call} jobId={detail.id} question={detail.question} />
        ) : null}
      </header>

      <div className="flex gap-6 border-b border-white/[0.06] px-4">
        {(['findings', 'journal'] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={cn(
              'border-b py-3 font-mono text-[10px] uppercase tracking-[0.1em] transition-colors',
              tab === t
                ? 'border-white/70 text-white'
                : 'border-transparent text-white/35 hover:text-white/60',
            )}
          >
            {t === 'findings' ? `Findings · ${detail.results.length}` : `Journal · ${detail.steps.length}`}
          </button>
        ))}
      </div>

      {tab === 'findings' ? (
        <FindingsTable
          findings={detail.results}
          walletAddress={detail.wallet_address}
        />
      ) : (
        <Journal steps={detail.steps} />
      )}
    </section>
  )
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone?: 'danger' | 'warn' | 'good'
}) {
  return (
    <div className="rounded border border-white/[0.07] px-3 py-2.5">
      <dt className="font-mono text-[9px] uppercase tracking-[0.1em] text-white/35">
        {label}
      </dt>
      <dd
        className={cn(
          'display mt-1 text-xl',
          tone === 'danger' && value > 0 && 'text-red-300',
          tone === 'warn' && value > 0 && 'text-amber-200',
          tone === 'good' && value > 0 && 'text-emerald-200',
          !tone && 'text-white/70',
        )}
      >
        {value}
      </dd>
    </div>
  )
}

/** The agent's clarification dialog (ADR-032): answering resumes the job. */
function AnswerForm({
  call,
  jobId,
  question,
}: {
  call: Call
  jobId: string
  question: string
}) {
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    try {
      await call((t) => api.answerScan(jobId, answer, t))
      setAnswer('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-2 rounded border border-amber-400/25 bg-amber-400/[0.06] p-3"
    >
      <p className="text-xs leading-relaxed text-amber-100">{question}</p>
      <div className="flex gap-2">
        <label htmlFor="answer" className="sr-only">
          Your answer
        </label>
        <input
          id="answer"
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          className="h-10 min-w-0 flex-1 rounded border border-white/[0.12] bg-black/40 px-3 font-mono text-xs text-white focus:border-white/40 focus:outline-none"
        />
        <button
          type="submit"
          disabled={busy || !answer.trim()}
          className="h-10 shrink-0 rounded-full bg-white px-5 text-xs font-medium text-black transition-opacity hover:opacity-85 disabled:opacity-40"
        >
          {busy ? 'Sending…' : 'Answer'}
        </button>
      </div>
    </form>
  )
}
