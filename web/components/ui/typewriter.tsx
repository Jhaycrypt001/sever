'use client'

import { useEffect, useState } from 'react'
import { useReducedMotion } from 'motion/react'

export interface TypewriterProps {
  text: string | string[]
  speed?: number
  cursor?: string
  loop?: boolean
  deleteSpeed?: number
  delay?: number
  className?: string
}

/**
 * Types text out one character at a time, optionally cycling a list.
 *
 * Under `prefers-reduced-motion` it renders the first string outright and
 * stops there: an animated cursor that never settles is exactly the kind of
 * perpetual movement that setting exists to switch off.
 */
export function Typewriter({
  text,
  speed = 100,
  cursor = '|',
  loop = false,
  deleteSpeed = 50,
  delay = 1500,
  className,
}: TypewriterProps) {
  const reduce = useReducedMotion()
  const textArray = Array.isArray(text) ? text : [text]

  const [displayText, setDisplayText] = useState('')
  const [charIndex, setCharIndex] = useState(0)
  const [isDeleting, setIsDeleting] = useState(false)
  const [phrase, setPhrase] = useState(0)

  const currentText = textArray[phrase] ?? ''

  useEffect(() => {
    if (reduce || !currentText) return

    const timeout = setTimeout(
      () => {
        if (!isDeleting) {
          if (charIndex < currentText.length) {
            setDisplayText(currentText.slice(0, charIndex + 1))
            setCharIndex((i) => i + 1)
          } else if (loop) {
            setTimeout(() => setIsDeleting(true), delay)
          }
        } else if (displayText.length > 0) {
          setDisplayText((prev) => prev.slice(0, -1))
        } else {
          setIsDeleting(false)
          setCharIndex(0)
          setPhrase((p) => (p + 1) % textArray.length)
        }
      },
      isDeleting ? deleteSpeed : speed,
    )

    return () => clearTimeout(timeout)
  }, [
    charIndex,
    currentText,
    delay,
    deleteSpeed,
    displayText,
    isDeleting,
    loop,
    reduce,
    speed,
    textArray.length,
  ])

  if (reduce) return <span className={className}>{textArray[0]}</span>

  return (
    <span className={className}>
      {displayText}
      <span aria-hidden="true" className="animate-pulse">
        {cursor}
      </span>
    </span>
  )
}
