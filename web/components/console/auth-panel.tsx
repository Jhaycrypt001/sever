'use client'

import Link from 'next/link'
import { useState } from 'react'
import { motion } from 'motion/react'
import { ApprovalMap } from '@/components/firewall/approval-map'
import { PasswordInput } from '@/components/ui/password-input'
import { Typewriter } from '@/components/ui/typewriter'
import { ApiError, api } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Sign-in gate for the console.
 *
 * The access token is handed to the caller and held in React state only
 * (ADR-008). The refresh token lives in an HttpOnly cookie the browser
 * manages, which is why the console proxies `/api` same-origin.
 *
 * Deliberately absent, because each would be a control that lies:
 *
 * - No "continue with Google". There is no OAuth provider wired up, and a
 *   button that logs to the console is worse than no button.
 * - No full-name field. `POST /api/auth/register` takes an email and a
 *   password; a field whose value is dropped on the floor is a fiction.
 * - No wallet connect. Scanning is read-only and revocation runs on the
 *   wallet delegated to KeeperHub, so the account has no wallet to prove
 *   ownership of yet. See the note in ADR-061 before adding one.
 */
const EASE = [0.16, 1, 0.3, 1] as const

/** Truthful lines. Each one is a claim the rest of the site backs up. */
const MARQUEE = [
  'Every approval you have ever signed is still live.',
  'A drainer does not need a new exploit.',
  'It only needs the permission you already gave.',
]

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

  const isLogin = mode === 'login'

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (!isLogin) await api.register(email, password)
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
    <div className="grid min-h-[100svh] w-full lg:grid-cols-[1fr_minmax(0,44%)]">
      {/* ---------------------------------------------------------- form */}
      <div className="flex items-center justify-center px-6 py-12 md:px-10">
        <div className="w-full max-w-[380px]">
          <Link
            href="/"
            className="mb-12 flex items-center gap-3 text-foreground transition-opacity hover:opacity-70"
          >
            <svg
              viewBox="0 0 32 32"
              className="size-6 shrink-0"
              aria-hidden="true"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinecap="round"
            >
              <path d="M16 2.5 4.5 7v9c0 6.6 4.8 11.6 11.5 13.5C22.7 27.6 27.5 22.6 27.5 16V7L16 2.5Z" />
              <path d="M13.4 13 11.8 14.6a3.4 3.4 0 0 0 4.8 4.8l1.6-1.6" />
              <path d="M18.6 19 20.2 17.4a3.4 3.4 0 0 0-4.8-4.8l-1.6 1.6" />
            </svg>
            <span className="display text-lg leading-none">
              Approval Firewall
            </span>
          </Link>

          <motion.div
            key={mode}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.45, ease: EASE }}
          >
            <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/40">
              {isLogin ? '§ Console access' : '§ New account'}
            </span>

            <h1 className="display mt-3 text-3xl text-foreground md:text-4xl">
              {isLogin ? 'Sign in' : 'Create an account'}
            </h1>

            <p className="mt-3 text-sm leading-relaxed text-white/45">
              {isLogin
                ? 'Scan a wallet, watch the dangerous approvals get revoked, and read the receipts.'
                : 'One account watches as many wallets as you point it at.'}
            </p>
          </motion.div>

          <form onSubmit={submit} className="mt-8 flex flex-col gap-4">
            <div className="flex w-full flex-col gap-1.5">
              <label
                htmlFor="auth-email"
                className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40"
              >
                Email
              </label>
              <input
                id="auth-email"
                name="email"
                type="email"
                value={email}
                required
                autoComplete="email"
                placeholder="you@example.com"
                onChange={(e) => setEmail(e.target.value)}
                className="h-11 rounded border border-white/[0.12] bg-black/40 px-3 font-mono text-sm text-white placeholder:text-white/25 focus:border-white/40 focus:outline-none"
              />
            </div>

            <PasswordInput
              id="auth-password"
              name="password"
              label="Password"
              required
              value={password}
              placeholder={isLogin ? 'Your password' : 'At least 12 characters'}
              autoComplete={isLogin ? 'current-password' : 'new-password'}
              onChange={(e) => setPassword(e.target.value)}
            />

            {error ? (
              <motion.p
                role="alert"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                transition={{ duration: 0.3, ease: EASE }}
                className="overflow-hidden rounded border border-red-500/25 bg-red-500/10 px-3 py-2 font-mono text-[11px] text-red-300"
              >
                {error}
              </motion.p>
            ) : null}

            <button
              type="submit"
              disabled={busy || !email || !password}
              className={cn(
                'mt-2 h-11 rounded-full bg-white px-6 text-sm font-medium text-black',
                'transition-opacity hover:opacity-85 disabled:opacity-40',
              )}
            >
              {busy ? 'Working…' : isLogin ? 'Sign in' : 'Create account'}
            </button>
          </form>

          <button
            type="button"
            onClick={() => {
              setMode(isLogin ? 'register' : 'login')
              setError(null)
            }}
            className="mt-6 font-mono text-[10px] uppercase tracking-[0.08em] text-white/40 transition-colors hover:text-white"
          >
            {isLogin ? 'No account? Create one' : 'Already registered? Sign in'}
          </button>

          <p className="mt-10 border-t border-white/[0.08] pt-6 font-mono text-[10px] uppercase leading-relaxed tracking-[0.08em] text-white/30">
            No private key · no seed phrase · no wallet signature
            <br />
            Revocations are executed by KeeperHub
          </p>
        </div>
      </div>

      {/* -------------------------------------------------------- plate */}
      <aside className="relative hidden overflow-hidden border-l border-white/[0.08] lg:block">
        <div className="absolute inset-0">
          <ApprovalMap animated={false} legend={false} />
        </div>

        <div
          aria-hidden="true"
          className="absolute inset-x-0 bottom-0 h-2/5"
          style={{
            background:
              'linear-gradient(to top, oklch(0.145 0 0) 10%, transparent 100%)',
          }}
        />

        <div className="relative z-10 flex h-full flex-col justify-end p-10">
          <p className="min-h-[3.5rem] text-pretty text-lg leading-snug text-foreground">
            <Typewriter text={MARQUEE} speed={45} deleteSpeed={22} loop />
          </p>
          <p className="mt-4 font-mono text-[10px] uppercase tracking-[0.12em] text-white/35">
            Attack surface · one wallet, every spender it trusts
          </p>
        </div>
      </aside>
    </div>
  )
}
