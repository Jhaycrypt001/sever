'use client'

import {
  motion,
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
  type MotionValue,
} from 'motion/react'
import { useRef } from 'react'

/**
 * A word whose letters fold open as it is scrolled into view and fold shut
 * again on the way back up.
 *
 * Driven by scroll position rather than by a viewport trigger, which is the
 * whole point: a `whileInView` animation fires once and then sits there, so
 * scrolling back up leaves the word already open. Mapping each letter to its
 * own slice of the section's scroll progress makes the motion reversible and
 * scrubbable, and gives the stagger for free.
 */
function Letter({
  char,
  progress,
  index,
  total,
}: {
  char: string
  progress: MotionValue<number>
  index: number
  total: number
}) {
  // Each letter opens over its own window; the windows overlap so the word
  // reads as one gesture rather than nine separate ones.
  const start = (index / total) * 0.55
  const end = start + 0.45

  const rotateX = useTransform(progress, [start, end], [88, 0])
  const y = useTransform(progress, [start, end], ['46%', '0%'])
  const opacity = useTransform(progress, [start, start + 0.12], [0, 1])

  return (
    <span
      aria-hidden="true"
      className="inline-block [perspective:600px]"
      style={{ perspective: 600 }}
    >
      <motion.span
        className="inline-block text-[clamp(2.5rem,11.5vw,10rem)] leading-none [transform-origin:50%_100%]"
        style={{ rotateX, y, opacity }}
      >
        {char}
      </motion.span>
    </span>
  )
}

export function FoldingWord({
  word,
  label,
  className,
}: {
  word: string
  label: string
  className?: string
}) {
  const ref = useRef<HTMLParagraphElement>(null)
  const reduce = useReducedMotion()

  const { scrollYProgress } = useScroll({
    target: ref,
    // Opens across the approach and is fully open once it sits mid-screen.
    offset: ['start 95%', 'start 40%'],
  })
  const progress = useSpring(scrollYProgress, {
    stiffness: 140,
    damping: 30,
    restDelta: 0.001,
  })

  const letters = word.split('')

  if (reduce) {
    return (
      <p aria-label={label} className={className}>
        {letters.map((ch, i) => (
          <span
            key={`${ch}-${i}`}
            aria-hidden="true"
            className="text-[clamp(2.5rem,11.5vw,10rem)] leading-none"
          >
            {ch}
          </span>
        ))}
      </p>
    )
  }

  return (
    <p ref={ref} aria-label={label} className={className}>
      {letters.map((ch, i) => (
        <Letter
          key={`${ch}-${i}`}
          char={ch}
          progress={progress}
          index={i}
          total={letters.length}
        />
      ))}
    </p>
  )
}
