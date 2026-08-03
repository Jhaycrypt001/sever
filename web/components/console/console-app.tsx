'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '@/lib/api'
import { AuthPanel } from './auth-panel'
import { Workspace } from './workspace'

/**
 * Session shell for the console.
 *
 * The access token is short-lived and kept in memory only (ADR-008). The
 * refresh token is an HttpOnly cookie, so a reload can restore the session
 * without the token ever having been readable by script: on mount we simply
 * ask `/api/auth/refresh` and see whether the browser had a valid cookie.
 */
export function ConsoleApp({
  initialAddress = '',
}: {
  initialAddress?: string
}) {
  const [token, setToken] = useState<string | null>(null)
  const [restoring, setRestoring] = useState(true)
  const tokenRef = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .refresh()
      .then(({ access_token }) => {
        if (cancelled) return
        tokenRef.current = access_token
        setToken(access_token)
      })
      .catch(() => {
        // No cookie, or it expired — the sign-in form is the correct answer.
      })
      .finally(() => {
        if (!cancelled) setRestoring(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const authenticate = useCallback((next: string) => {
    tokenRef.current = next
    setToken(next)
  }, [])

  /**
   * Runs an API call with a live token, rotating it once on a 401 rather than
   * dropping the operator back to the sign-in form mid-scan. A second 401
   * means the refresh cookie is gone too, and signing in again is the honest
   * outcome.
   */
  const call = useCallback(async function call<T>(
    fn: (token: string) => Promise<T>,
  ): Promise<T> {
    const current = tokenRef.current
    if (!current) throw new ApiError(401, 'signed out')
    try {
      return await fn(current)
    } catch (e) {
      if (!(e instanceof ApiError) || e.status !== 401) throw e
      const { access_token } = await api.refresh().catch(() => {
        tokenRef.current = null
        setToken(null)
        throw e
      })
      tokenRef.current = access_token
      setToken(access_token)
      return await fn(access_token)
    }
  }, [])

  const signOut = useCallback(async () => {
    await api.logout().catch(() => {
      // A failed logout still ends the local session; the cookie expires.
    })
    tokenRef.current = null
    setToken(null)
  }, [])

  if (restoring) {
    return (
      <p className="py-24 text-center font-mono text-xs text-white/35">
        Restoring session…
      </p>
    )
  }

  if (!token) return <AuthPanel onAuthenticated={authenticate} />

  return (
    <Workspace
      call={call}
      token={token}
      initialAddress={initialAddress}
      onSignOut={signOut}
    />
  )
}
