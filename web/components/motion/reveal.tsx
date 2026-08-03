'use client'

import { motion, useReducedMotion } from 'motion/react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Scroll-reveal primitives.
 *
 * Every one of these collapses to "render the content, no movement" when the
 * viewer has asked for reduced motion. That is not politeness: this is a
 * security product, and a page that lurches while someone is reading a risk
 * tier is actively worse than a static one.
 */

const EASE = [0.16, 1, 0.3, 1] as const

export function Reveal({
  children,
  className,
  delay = 0,
  y = 28,
  once = true,
}: {
  children: ReactNode
  className?: string
  delay?: number
  y?: number
  once?: boolean
}) {
  const reduce = useReducedMotion()

  if (reduce) return <div className={className}>{children}</div>

  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once, margin: '0px 0px -12% 0px' }}
      transition={{ duration: 0.7, delay, ease: EASE }}
    >
      {children}
    </motion.div>
  )
}

/**
 * Staggers its direct children. Pair with `RevealItem` — the parent owns the
 * viewport trigger so the children fire in sequence rather than each waiting
 * for its own intersection.
 */
export function RevealGroup({
  children,
  className,
  stagger = 0.08,
  delay = 0,
  once = true,
}: {
  children: ReactNode
  className?: string
  stagger?: number
  delay?: number
  once?: boolean
}) {
  const reduce = useReducedMotion()

  if (reduce) return <div className={className}>{children}</div>

  return (
    <motion.div
      className={className}
      initial="hidden"
      whileInView="shown"
      viewport={{ once, margin: '0px 0px -10% 0px' }}
      variants={{
        hidden: {},
        shown: { transition: { staggerChildren: stagger, delayChildren: delay } },
      }}
    >
      {children}
    </motion.div>
  )
}

export function RevealItem({
  children,
  className,
  y = 22,
  ...handlers
}: {
  children: ReactNode
  className?: string
  y?: number
  onMouseEnter?: () => void
  onFocus?: () => void
}) {
  const reduce = useReducedMotion()

  if (reduce) {
    return (
      <div className={className} {...handlers}>
        {children}
      </div>
    )
  }

  return (
    <motion.div
      className={className}
      variants={{
        hidden: { opacity: 0, y },
        shown: { opacity: 1, y: 0, transition: { duration: 0.65, ease: EASE } },
      }}
      {...handlers}
    >
      {children}
    </motion.div>
  )
}

/**
 * Word-by-word rise for a heading. Words, not letters: a headline that
 * assembles character by character is unreadable while it animates, and this
 * page has to be read.
 */
export function RevealWords({
  text,
  className,
  delay = 0,
}: {
  text: string
  className?: string
  delay?: number
}) {
  const reduce = useReducedMotion()
  const words = text.split(' ')

  if (reduce) return <span className={className}>{text}</span>

  return (
    <motion.span
      className={cn('inline-block', className)}
      initial="hidden"
      whileInView="shown"
      viewport={{ once: true, margin: '0px 0px -10% 0px' }}
      variants={{
        hidden: {},
        shown: { transition: { staggerChildren: 0.045, delayChildren: delay } },
      }}
    >
      {words.map((word, i) => (
        <span
          key={`${word}-${i}`}
          className="inline-block overflow-hidden align-bottom"
        >
          <motion.span
            className="inline-block"
            variants={{
              hidden: { y: '110%' },
              shown: { y: 0, transition: { duration: 0.8, ease: EASE } },
            }}
          >
            {word}
            {i < words.length - 1 ? ' ' : ''}
          </motion.span>
        </span>
      ))}
    </motion.span>
  )
}
