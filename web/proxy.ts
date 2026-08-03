import { NextResponse, type NextRequest } from 'next/server'

/**
 * Security headers for the app responses (ADR-054, ported to the Next runtime
 * in ADR-061). nginx used to add these in front of the Vue build; there is no
 * nginx in front of the console any more, so the app sets them itself.
 *
 * The CSP is nonce-based rather than `'unsafe-inline'`: the console holds a
 * live access token in memory, so an injected inline script is a token theft,
 * not a defacement. Next reads the nonce back out of the request CSP header
 * and stamps it on its own bootstrap scripts. `strict-dynamic` lets those
 * scripts load the chunks they need without the policy having to enumerate
 * them.
 *
 * The API sets its own, stricter headers on `/api/*` — those responses are
 * proxied through untouched.
 */
export default function proxy(request: NextRequest) {
  const nonce = crypto.randomUUID().replace(/-/g, '')

  // React's development build uses eval() to reconstruct callstacks. It never
  // does in production, so the shipped policy stays free of 'unsafe-eval' and
  // only `next dev` relaxes it.
  const devEval =
    process.env.NODE_ENV === 'production' ? '' : " 'unsafe-eval'"

  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${devEval}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self' data:",
    // Same-origin only: the API is proxied under /api, so nothing here needs
    // to reach a third-party host.
    "connect-src 'self'",
    "form-action 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
  ].join('; ')

  const headers = new Headers(request.headers)
  headers.set('x-nonce', nonce)
  headers.set('content-security-policy', csp)

  const response = NextResponse.next({ request: { headers } })
  response.headers.set('content-security-policy', csp)
  response.headers.set('x-content-type-options', 'nosniff')
  response.headers.set('x-frame-options', 'DENY')
  response.headers.set('referrer-policy', 'no-referrer')
  return response
}

export const config = {
  // Static assets are immutable and carry no script context; skipping them
  // keeps the nonce off cacheable responses.
  matcher: [
    {
      source: '/((?!api|_next/static|_next/image|favicon.ico|images).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' },
      ],
    },
  ],
}
