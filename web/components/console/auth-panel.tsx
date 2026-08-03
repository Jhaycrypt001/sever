'use client'

import { useState } from 'react'
import { ApiError, api } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Sign-in gate for the console.
 *
 * The access token is handed to the caller and held in React state only —
 * never localStorage (ADR-008). The refresh token lives in an HttpOnly cookie
 * the browser manages, which is why the console proxies `/api` same-origin.
 */
export function AuthPanel({
  onAuthenticated,
}: {
  onAuthenticated: (token: string) => void
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'register') await api.register(email, password)
      const { access_token } = await api.login(email, password)
      onAuthenticated(access_token)
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : 'Could not reach the API. Is the backend running?',
      )
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-sm flex-col gap-6 rounded-lg border border-white/[0.08] bg-white/[0.015] p-7">
      <div className="flex flex-col gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/40">
          Console
        </span>
        <h1 className="display text-2xl text-white">
          {mode === 'login' ? 'Sign in' : 'Create an account'}
        </h1>
        <p className="text-xs leading-relaxed text-white/45">
          Scanning is read-only. The console never asks for a private key, a
          seed phrase or a wallet signature. Revocations are executed by
          KeeperHub.
        </p>
      </div>

      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field
          label="Email"
          type="email"
          value={email}
          autoComplete="email"
          onChange={setEmail}
        />
        <Field
          label="Password"
          type="password"
          value={password}
          autoComplete={
            mode === 'login' ? 'current-password' : 'new-password'
          }
          onChange={setPassword}
        />

        {error ? (
          <p
            role="alert"
            className="rounded border border-red-500/25 bg-red-500/10 px-3 py-2 font-mono text-[11px] text-red-300"
          >
            {error}
          </p>
        ) : null}

        <button
          type="submit"
          disabled={busy || !email || !password}
          className="mt-1 rounded-full bg-white px-6 py-3 text-sm font-medium text-black transition-opacity hover:opacity-85 disabled:opacity-40"
        >
          {busy
            ? 'Working…'
            : mode === 'login'
              ? 'Sign in'
              : 'Create account'}
        </button>
      </form>

      <button
        type="button"
        onClick={() => {
          setMode(mode === 'login' ? 'register' : 'login')
          setError(null)
        }}
        className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40 transition-colors hover:text-white/70"
      >
        {mode === 'login'
          ? 'No account? Create one'
          : 'Already registered? Sign in'}
      </button>
    </div>
  )
}

function Field({
  label,
  type,
  value,
  autoComplete,
  onChange,
}: {
  label: string
  type: string
  value: string
  autoComplete: string
  onChange: (value: string) => void
}) {
  const id = `auth-${label.toLowerCase()}`
  return (
    <label htmlFor={id} className="flex flex-col gap-1.5">
      <span className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40">
        {label}
      </span>
      <input
        id={id}
        type={type}
        value={value}
        required
        autoComplete={autoComplete}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          'h-11 rounded border border-white/[0.12] bg-black/40 px-3 font-mono text-sm text-white',
          'focus:border-white/40 focus:outline-none',
        )}
      />
    </label>
  )
}
