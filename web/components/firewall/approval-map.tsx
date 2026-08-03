'use client'

import { motion, useReducedMotion } from 'motion/react'

/**
 * The plate on the spec sheet.
 *
 * It replaces a stock macro photograph of a mineral surface, which was
 * handsome and meant nothing. What a wallet actually has is an attack surface:
 * one address in the middle, and a live spending edge out to every contract it
 * has ever approved. Drawing that is both the better picture and the argument,
 * because the dangerous edges are the ones this product cuts.
 *
 * Geometry is computed from the index rather than sampled randomly, so the
 * server and the client draw the same thing and React has no hydration
 * mismatch to reconcile.
 */

type Tier = 'safe' | 'watch' | 'dangerous'

const CENTER = { x: 200, y: 250 }
const COUNT = 15

const TIER_OF: Tier[] = Array.from({ length: COUNT }, (_, i) =>
  i % 7 === 3 ? 'dangerous' : i % 3 === 1 ? 'watch' : 'safe',
)

const NODES = Array.from({ length: COUNT }, (_, i) => {
  const angle = (i / COUNT) * Math.PI * 2 - Math.PI / 2
  // Deterministic jitter: keeps the ring from looking like a clock face.
  const radius = 116 + ((i * 41) % 58)
  const tier = TIER_OF[i]
  const x = CENTER.x + Math.cos(angle) * radius
  const y = CENTER.y + Math.sin(angle) * radius * 1.2
  return { i, x, y, tier, angle, radius }
})

/** Derived, so the legend can never drift from what is drawn. */
const CUT_COUNT = NODES.filter((n) => n.tier === 'dangerous').length

const STROKE: Record<Tier, string> = {
  safe: 'rgba(255,255,255,0.13)',
  watch: 'rgba(251,191,36,0.34)',
  dangerous: 'rgba(248,113,113,0.55)',
}

const FILL: Record<Tier, string> = {
  safe: 'rgba(255,255,255,0.28)',
  watch: 'rgba(251,191,36,0.75)',
  dangerous: 'rgba(248,113,113,0.95)',
}

/** Point a fraction of the way from the wallet out to a spender. */
function along(node: (typeof NODES)[number], t: number) {
  return {
    x: CENTER.x + (node.x - CENTER.x) * t,
    y: CENTER.y + (node.y - CENTER.y) * t,
  }
}

export function ApprovalMap() {
  const reduce = useReducedMotion()

  return (
    <svg
      viewBox="0 0 400 500"
      className="size-full"
      role="img"
      aria-label="A wallet at the centre with a spending edge out to every contract it has approved. The edges flagged dangerous are cut."
    >
      <defs>
        <radialGradient id="afw-plate" cx="50%" cy="42%" r="72%">
          <stop offset="0%" stopColor="#1b1b1b" />
          <stop offset="60%" stopColor="#101010" />
          <stop offset="100%" stopColor="#070707" />
        </radialGradient>
        <radialGradient id="afw-core" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="rgba(255,255,255,0.95)" />
          <stop offset="55%" stopColor="rgba(255,255,255,0.35)" />
          <stop offset="100%" stopColor="rgba(255,255,255,0)" />
        </radialGradient>
        <radialGradient id="afw-sweep" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="rgba(255,255,255,0.16)" />
          <stop offset="100%" stopColor="rgba(255,255,255,0)" />
        </radialGradient>
      </defs>

      <rect width="400" height="500" fill="url(#afw-plate)" />

      {/* measurement grid, faint */}
      <g stroke="rgba(255,255,255,0.045)" strokeWidth="0.5">
        {Array.from({ length: 9 }, (_, i) => (
          <line key={`v${i}`} x1={(i + 1) * 40} y1="0" x2={(i + 1) * 40} y2="500" />
        ))}
        {Array.from({ length: 11 }, (_, i) => (
          <line key={`h${i}`} x1="0" y1={(i + 1) * 41.6} x2="400" y2={(i + 1) * 41.6} />
        ))}
      </g>

      {/* range rings */}
      <g fill="none" stroke="rgba(255,255,255,0.07)" strokeWidth="0.6">
        <ellipse cx={CENTER.x} cy={CENTER.y} rx="88" ry="105" />
        <ellipse cx={CENTER.x} cy={CENTER.y} rx="132" ry="158" />
        <ellipse cx={CENTER.x} cy={CENTER.y} rx="176" ry="211" />
      </g>

      {/* the scan sweep: the recurring run coming back around */}
      {!reduce ? (
        <motion.g
          style={{ originX: '200px', originY: '250px' }}
          animate={{ rotate: 360 }}
          transition={{ duration: 24, ease: 'linear', repeat: Infinity }}
        >
          <path
            d={`M ${CENTER.x} ${CENTER.y} L ${CENTER.x} ${CENTER.y - 230} A 230 230 0 0 1 ${
              CENTER.x + 132
            } ${CENTER.y - 188} Z`}
            fill="url(#afw-sweep)"
          />
        </motion.g>
      ) : null}

      {/* approval edges */}
      <g>
        {NODES.map((n) => {
          if (n.tier !== 'dangerous') {
            return (
              <line
                key={`edge-${n.i}`}
                x1={CENTER.x}
                y1={CENTER.y}
                x2={n.x}
                y2={n.y}
                stroke={STROKE[n.tier]}
                strokeWidth={n.tier === 'watch' ? 1.1 : 0.8}
              />
            )
          }

          // A cut edge: drawn to the break, then resumed past it, so the gap
          // reads as severed rather than as a line that was never there.
          const cutA = along(n, 0.46)
          const cutB = along(n, 0.62)
          return (
            <g key={`edge-${n.i}`}>
              <line
                x1={CENTER.x}
                y1={CENTER.y}
                x2={cutA.x}
                y2={cutA.y}
                stroke={STROKE.dangerous}
                strokeWidth="1.4"
              />
              <line
                x1={cutB.x}
                y1={cutB.y}
                x2={n.x}
                y2={n.y}
                stroke="rgba(248,113,113,0.2)"
                strokeWidth="1.4"
                strokeDasharray="2 3"
              />
              {/* the cut mark */}
              <line
                x1={cutA.x - (n.y - CENTER.y) * 0.035}
                y1={cutA.y + (n.x - CENTER.x) * 0.035}
                x2={cutA.x + (n.y - CENTER.y) * 0.035}
                y2={cutA.y - (n.x - CENTER.x) * 0.035}
                stroke="rgba(248,113,113,0.9)"
                strokeWidth="1.6"
                strokeLinecap="round"
              />
            </g>
          )
        })}
      </g>

      {/* spender nodes */}
      <g>
        {NODES.map((n) => (
          <g key={`node-${n.i}`}>
            {n.tier === 'dangerous' && !reduce ? (
              <motion.circle
                cx={n.x}
                cy={n.y}
                r="4"
                fill="none"
                stroke="rgba(248,113,113,0.6)"
                strokeWidth="1"
                animate={{ r: [4, 13], opacity: [0.6, 0] }}
                transition={{
                  duration: 2.6,
                  repeat: Infinity,
                  delay: n.i * 0.35,
                  ease: 'easeOut',
                }}
              />
            ) : null}
            <circle
              cx={n.x}
              cy={n.y}
              r={n.tier === 'safe' ? 2.2 : 3.2}
              fill={FILL[n.tier]}
            />
            {n.tier !== 'safe' ? (
              <circle
                cx={n.x}
                cy={n.y}
                r="7"
                fill="none"
                stroke={STROKE[n.tier]}
                strokeWidth="0.7"
              />
            ) : null}
          </g>
        ))}
      </g>

      {/* the wallet */}
      <circle cx={CENTER.x} cy={CENTER.y} r="46" fill="url(#afw-core)" />
      <circle
        cx={CENTER.x}
        cy={CENTER.y}
        r="15"
        fill="#0a0a0a"
        stroke="rgba(255,255,255,0.5)"
        strokeWidth="1"
      />
      {/* shield over a severed link, same mark as the wordmark */}
      <g
        transform={`translate(${CENTER.x - 8} ${CENTER.y - 8}) scale(0.5)`}
        fill="none"
        stroke="rgba(255,255,255,0.9)"
        strokeWidth="2"
        strokeLinecap="round"
      >
        <path d="M16 2.5 4.5 7v9c0 6.6 4.8 11.6 11.5 13.5C22.7 27.6 27.5 22.6 27.5 16V7L16 2.5Z" />
      </g>

      {/* legend */}
      <g fontFamily="var(--font-mono), monospace" fontSize="8" letterSpacing="1.2">
        <text x="24" y="466" fill="rgba(255,255,255,0.35)">
          {COUNT} SPENDERS
        </text>
        <text x="24" y="480" fill="rgba(248,113,113,0.8)">
          {CUT_COUNT} CUT
        </text>
        <text x="376" y="466" textAnchor="end" fill="rgba(255,255,255,0.35)">
          ATTACK SURFACE
        </text>
        <text x="376" y="480" textAnchor="end" fill="rgba(255,255,255,0.35)">
          AFW / AF-01
        </text>
      </g>
    </svg>
  )
}
