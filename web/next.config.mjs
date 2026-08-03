import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'

/**
 * Where the Rust API lives. The console proxies `/api/*` here rather than
 * calling it cross-origin, because the refresh token is an HttpOnly cookie
 * (ADR-008): the browser only attaches it to same-origin requests. This
 * mirrors what nginx does in front of the built site.
 *
 * Parsed rather than interpolated — a rewrite destination assembled from an
 * unvalidated string is the shape of Next's rewrite SSRF advisory, and a typo
 * here should fail the boot, not silently proxy somewhere else.
 */
function apiOrigin() {
  const raw = process.env.API_ORIGIN ?? 'http://localhost:8000'
  const url = new URL(raw)
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`API_ORIGIN must be http(s), got ${url.protocol}`)
  }
  return url.origin
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Self-contained server bundle for the runtime image (ADR-014): no
  // node_modules copy, no npm install in the final layer.
  output: 'standalone',
  // The template shipped with ignoreBuildErrors: true, which would let a type
  // error reach production silently. This site typechecks clean, so the build
  // stays honest and fails loudly instead.
  typescript: {
    ignoreBuildErrors: false,
  },
  images: {
    unoptimized: true,
  },
  // This app lives inside a monorepo; without an explicit root, Turbopack walks
  // up and picks a lockfile outside the project.
  turbopack: {
    root: dirname(fileURLToPath(import.meta.url)),
  },
  async rewrites() {
    const origin = apiOrigin()
    return [{ source: '/api/:path*', destination: `${origin}/api/:path*` }]
  },
}

export default nextConfig
