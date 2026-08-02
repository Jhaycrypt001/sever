import { fileURLToPath } from 'node:url'
import { dirname } from 'node:path'

/** @type {import('next').NextConfig} */
const nextConfig = {
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
}

export default nextConfig
