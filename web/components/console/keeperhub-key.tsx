'use client'

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api, type KeeperHubKey } from '@/lib/api'
import { truncateAddress } from '@/lib/chains'

type Call = <T>(fn: (token: string) => Promise<T>) => Promise<T>

/**
 * Connect the account's own KeeperHub API key (ADR-076).
 *
 * KeeperHub executes as the single wallet delegated to a key (ADR-065), so
 * without the owner's own key every revocation for their wallet is refused.
 * This panel is the only place that key is ever typed, and it is write-only:
 * once sent it never comes back, so the panel can show which key is connected
 * (masked) and which wallet it executes as, but never the key.
 *
 * The whole feature is optional. A deployment with no encryption key answers
 * 501, and the panel then renders nothing rather than offering a control that
 * cannot work.
 */
export function KeeperHubKeyPanel({ call }: { call: Call }) {
  const [connected, setConnected] = useState<KeeperHubKey | null>(null)
  const [available, setAvailable] = useState(true)
  const [apiKey, setApiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setConnected(await call((t) => api.getKeeperHubKey(t)))
    } catch (e) {
      // 501: the feature is off on this deployment, not an error to report.
      if (e instanceof ApiError && e.status === 501) setAvailable(false)
    }
  }, [call])

  useEffect(() => {
    void refresh()
  }, [refresh])

  async function connect(event: React.FormEvent) {
    event.preventDefault()
    const key = apiKey.trim()
    if (!key) return
    setBusy(true)
    setError(null)
    try {
      setConnected(await call((t) => api.connectKeeperHubKey(key, t)))
      // Cleared on success only: a rejected key stays in the field so a typo
      // can be corrected rather than re-pasted from scratch.
      setApiKey('')
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : 'Could not reach the API.',
      )
    } finally {
      setBusy(false)
    }
  }

  async function disconnect() {
    setBusy(true)
    setError(null)
    try {
      await call((t) => api.disconnectKeeperHubKey(t))
      setConnected(null)
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : 'Could not reach the API.',
      )
    } finally {
      setBusy(false)
    }
  }

  if (!available) return null

  return (
    <section
      data-testid="keeperhub-key-section"
      className="rounded-lg border border-white/[0.08] bg-white/[0.015]"
    >
      <header className="border-b border-white/[0.06] px-4 py-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/40">
          Revocation key
        </span>
      </header>

      {connected ? (
        <div className="flex flex-col gap-2 p-4">
          <span className="font-mono text-[11px] text-white/70">
            {connected.wallet_address
              ? `Executes as ${truncateAddress(connected.wallet_address, 8, 6)}`
              : 'Connected'}
          </span>
          <span className="font-mono text-[10px] text-white/30">
            Key {connected.masked}
          </span>
          <p className="font-mono text-[10px] leading-relaxed text-white/35">
            Sever can revoke approvals on that wallet, and only that one.
          </p>
          <button
            type="button"
            onClick={() => void disconnect()}
            disabled={busy}
            className="h-8 self-start font-mono text-[10px] uppercase tracking-[0.08em] text-white/30 transition-colors hover:text-red-300 disabled:opacity-30"
          >
            {busy ? 'Working…' : 'Disconnect'}
          </button>
        </div>
      ) : (
        <form onSubmit={connect} className="flex flex-col gap-2 p-4">
          <label htmlFor="keeperhub-key" className="sr-only">
            KeeperHub API key
          </label>
          <input
            id="keeperhub-key"
            // `password`, not `text`: this is typed on a screen that may well
            // be shared, and it keeps the value out of autofill history.
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="KeeperHub API key"
            spellCheck={false}
            autoComplete="off"
            className="h-9 rounded border border-white/[0.12] bg-black/40 px-2.5 font-mono text-xs text-white placeholder:text-white/25 focus:border-white/40 focus:outline-none"
          />
          <button
            type="submit"
            disabled={busy || !apiKey.trim()}
            className="h-9 rounded-full bg-white/90 text-xs font-medium text-black transition-opacity hover:opacity-85 disabled:opacity-30"
          >
            {busy ? 'Checking…' : 'Connect'}
          </button>
          <p className="font-mono text-[10px] leading-relaxed text-white/35">
            Without a key, scans still run — findings are reported but nothing
            is revoked. The key is checked against KeeperHub before it is
            stored, and is never shown again.
          </p>
          {error ? (
            <p role="alert" className="font-mono text-[10px] text-red-300">
              {error}
            </p>
          ) : null}
        </form>
      )}
    </section>
  )
}
