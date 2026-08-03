'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { ApprovalMap } from '@/components/firewall/approval-map'
import { PasswordInput } from '@/components/ui/password-input'
import { Typewriter } from '@/components/ui/typewriter'
import { ApiError, api } from '@/lib/api'
import { cn } from '@/lib/utils'

/**
 * Sign-in gate for the console.
 *
 * Four steps, because a session is never handed out for a password alone
 * (ADR-062/063):
 *
 * - `login` / `register` collect the credentials. Neither signs anyone in;
 *   both end by mailing a six-digit code.
 * - `verify` answers that code, and *that* is the sign-in.
 * - `forgot` / `reset` recover an account nobody can remember the password
 *   for, ending in the same signed-in state.
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

/** Seconds before "send another" becomes available again. */
const RESEND_COOLDOWN_SECONDS = 30

const CODE_LENGTH = 6

/** Matches the backend's minimum (`password_reset::MIN_PASSWORD_LENGTH`). */
const MIN_PASSWORD_LENGTH = 8

type Mode = 'login' | 'register' | 'verify' | 'forgot' | 'reset'

export function AuthPanel({
  onAuthenticated,
}: {
  onAuthenticated: (token: string) => void
}) {
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [cooldown, setCooldown] = useState(0)
  const codeRef = useRef<HTMLInputElement>(null)

  const isCodeStep = mode === 'verify' || mode === 'reset'

  // Cooldown tick. Guards the resend button against being hammered, which the
  // backend would throttle anyway — better to say so before the request.
  useEffect(() => {
    if (cooldown <= 0) return
    const timer = setTimeout(() => setCooldown((s) => s - 1), 1000)
    return () => clearTimeout(timer)
  }, [cooldown])

  // The code box is the first thing to fill on those steps, so put the caret
  // in it rather than making someone click.
  useEffect(() => {
    if (isCodeStep) codeRef.current?.focus()
  }, [isCodeStep])

  /**
   * Moves to a code step.
   *
   * `devCode` is only ever non-null against a backend running without a mail
   * provider. Showing it there is the difference between a console you can
   * work on locally and one that needs a mailbox; in production the field is
   * absent and this collapses to the ordinary "check your email".
   */
  const goToCodeStep = useCallback(
    (next: 'verify' | 'reset', devCode: string | null | undefined) => {
      setMode(next)
      setError(null)
      setCooldown(RESEND_COOLDOWN_SECONDS)
      if (devCode) {
        setCode(devCode)
        setNotice(`No mail provider configured — your code is ${devCode}`)
      } else {
        setCode('')
        setNotice(null)
      }
    },
    [],
  )

  function fail(e: unknown) {
    setError(
      e instanceof ApiError
        ? e.message
        : 'Could not reach the API. Is the backend running?',
    )
    setBusy(false)
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      switch (mode) {
        case 'verify': {
          const { access_token } = await api.verifyEmail(email, code)
          onAuthenticated(access_token)
          return
        }
        case 'reset': {
          const { access_token } = await api.resetPassword(
            email,
            code,
            password,
          )
          onAuthenticated(access_token)
          return
        }
        case 'register': {
          const created = await api.register(email, password)
          goToCodeStep('verify', created.verification_code)
          break
        }
        case 'forgot': {
          const { verification_code } = await api.forgotPassword(email)
          // Deliberately not "we sent you an email": the endpoint answers the
          // same way for an address with no account, so promising a delivery
          // would be a claim the API never made. The new password is typed on
          // the next step, alongside the code.
          setPassword('')
          goToCodeStep('reset', verification_code)
          break
        }
        case 'login': {
          // The password is one factor of two: this returns a code, never a
          // session (ADR-063).
          const { verification_code } = await api.login(email, password)
          goToCodeStep('verify', verification_code)
          break
        }
      }
      setBusy(false)
    } catch (e) {
      fail(e)
    }
  }

  async function resend() {
    setBusy(true)
    setError(null)
    try {
      // On the sign-in step the code is bound to the password that was just
      // accepted, so re-submitting the credentials is what issues a new one.
      // On the reset step the address alone is the input.
      const { verification_code } =
        mode === 'reset'
          ? await api.forgotPassword(email)
          : await api.login(email, password)
      setCooldown(RESEND_COOLDOWN_SECONDS)
      setCode('')
      setNotice(
        verification_code
          ? `No mail provider configured — your code is ${verification_code}`
          : 'If that address needs a code, a new one is on its way.',
      )
    } catch (e) {
      fail(e)
      return
    }
    setBusy(false)
  }

  function goTo(next: Mode) {
    setMode(next)
    setError(null)
    setNotice(null)
    setCode('')
  }

  const HEADINGS: Record<Mode, string> = {
    login: 'Sign in',
    register: 'Create an account',
    verify: 'Check your email',
    forgot: 'Reset your password',
    reset: 'Choose a new password',
  }

  const EYEBROWS: Record<Mode, string> = {
    login: '§ Console access',
    register: '§ New account',
    verify: '§ Two-factor',
    forgot: '§ Account recovery',
    reset: '§ Account recovery',
  }

  const BLURBS: Record<Mode, string> = {
    login:
      'Scan a wallet, watch the dangerous approvals get revoked, and read the receipts.',
    register: 'One account watches as many wallets as you point it at.',
    verify: `We sent a ${CODE_LENGTH}-digit code to ${email}. It expires in 10 minutes.`,
    forgot:
      'Enter your address and we will send a code you can use to set a new password.',
    reset: `Enter the code we sent to ${email}, and the password you want from now on.`,
  }

  const SUBMIT_LABELS: Record<Mode, string> = {
    login: 'Sign in',
    register: 'Create account',
    verify: 'Verify and continue',
    forgot: 'Send reset code',
    reset: 'Set password and sign in',
  }

  const wantsCode = isCodeStep
  const wantsEmail = mode !== 'verify' && mode !== 'reset'
  const wantsPassword = mode === 'login' || mode === 'register' || mode === 'reset'

  const canSubmit =
    (!wantsEmail || Boolean(email)) &&
    (!wantsCode || code.trim().length === CODE_LENGTH) &&
    (!wantsPassword ||
      (mode === 'reset'
        ? password.length >= MIN_PASSWORD_LENGTH
        : Boolean(password)))

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
              {EYEBROWS[mode]}
            </span>

            <h1 className="display mt-3 text-3xl text-foreground md:text-4xl">
              {HEADINGS[mode]}
            </h1>

            <p className="mt-3 text-sm leading-relaxed break-words text-white/45">
              {BLURBS[mode]}
            </p>
          </motion.div>

          <form onSubmit={submit} className="mt-8 flex flex-col gap-4">
            {wantsEmail ? (
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
            ) : null}

            {wantsCode ? (
              <div className="flex w-full flex-col gap-1.5">
                <label
                  htmlFor="auth-code"
                  className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40"
                >
                  Verification code
                </label>
                <input
                  ref={codeRef}
                  id="auth-code"
                  name="code"
                  value={code}
                  required
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  // A 6-digit box that silently accepts letters is a dead end:
                  // the API will refuse it and the person cannot see why.
                  pattern="[0-9]*"
                  maxLength={CODE_LENGTH}
                  placeholder="000000"
                  onChange={(e) =>
                    setCode(
                      e.target.value.replace(/\D/g, '').slice(0, CODE_LENGTH),
                    )
                  }
                  className="h-12 rounded border border-white/[0.12] bg-black/40 px-3 text-center font-mono text-xl tracking-[0.5em] text-white placeholder:text-white/20 focus:border-white/40 focus:outline-none"
                />
              </div>
            ) : null}

            {wantsPassword ? (
              <PasswordInput
                id="auth-password"
                name="password"
                label={mode === 'reset' ? 'New password' : 'Password'}
                required
                value={password}
                placeholder={
                  mode === 'login'
                    ? 'Your password'
                    : `At least ${MIN_PASSWORD_LENGTH} characters`
                }
                autoComplete={
                  mode === 'login' ? 'current-password' : 'new-password'
                }
                onChange={(e) => setPassword(e.target.value)}
              />
            ) : null}

            {error ? (
              <motion.p
                role="alert"
                // Next always renders an empty route announcer with role
                // "alert", so a role-based locator alone is ambiguous.
                data-testid="auth-error"
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                transition={{ duration: 0.3, ease: EASE }}
                className="overflow-hidden rounded border border-red-500/25 bg-red-500/10 px-3 py-2 font-mono text-[11px] text-red-300"
              >
                {error}
              </motion.p>
            ) : null}

            {notice ? (
              <p
                data-testid="verification-notice"
                className="rounded border border-amber-400/25 bg-amber-400/10 px-3 py-2 font-mono text-[11px] break-words text-amber-200"
              >
                {notice}
              </p>
            ) : null}

            <button
              type="submit"
              disabled={busy || !canSubmit}
              className={cn(
                'mt-2 h-11 rounded-full bg-white px-6 text-sm font-medium text-black',
                'transition-opacity hover:opacity-85 disabled:opacity-40',
              )}
            >
              {busy ? 'Working…' : SUBMIT_LABELS[mode]}
            </button>
          </form>

          <div className="mt-6 flex flex-wrap items-center gap-x-5 gap-y-2">
            {isCodeStep ? (
              <>
                <button
                  type="button"
                  onClick={resend}
                  disabled={busy || cooldown > 0}
                  className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40 transition-colors hover:text-white disabled:opacity-40 disabled:hover:text-white/40"
                >
                  {cooldown > 0 ? `Resend in ${cooldown}s` : 'Send another code'}
                </button>
                <button
                  type="button"
                  onClick={() => goTo('login')}
                  className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40 transition-colors hover:text-white"
                >
                  Use a different address
                </button>
              </>
            ) : mode === 'forgot' ? (
              <button
                type="button"
                onClick={() => goTo('login')}
                className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40 transition-colors hover:text-white"
              >
                Back to sign in
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => goTo(mode === 'login' ? 'register' : 'login')}
                  className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40 transition-colors hover:text-white"
                >
                  {mode === 'login'
                    ? 'No account? Create one'
                    : 'Already registered? Sign in'}
                </button>
                {mode === 'login' ? (
                  <button
                    type="button"
                    onClick={() => goTo('forgot')}
                    className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40 transition-colors hover:text-white"
                  >
                    Forgot password?
                  </button>
                ) : null}
              </>
            )}
          </div>

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
