'use client'

import { ChevronDown } from 'lucide-react'
import { motion } from 'motion/react'
import { useState, type SelectHTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * A `<select>` that looks styled but stays a `<select>`.
 *
 * The chevron is ours, animated on open; everything else — keyboard, mobile's
 * native picker, screen readers, `selectOption` in the e2e suite — is the
 * browser's, because the element underneath is unchanged. A div-and-buttons
 * menu would have to re-earn all of that, and typically re-earns only the
 * parts someone remembered to test.
 *
 * `open` tracks the popup only to spin the chevron. Browsers do not fire a
 * "select closed" event, so `blur` and `change` stand in: both end the
 * interaction, and a chevron left pointing the wrong way is the whole cost of
 * being wrong.
 */
export function SelectField({
  className,
  onChange,
  onBlur,
  onKeyDown,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative">
      <select
        {...props}
        onMouseDown={() => setOpen((wasOpen) => !wasOpen)}
        onKeyDown={(e) => {
          // Enter, space and the arrows are the keyboard ways into the popup.
          if ([' ', 'Enter', 'ArrowDown', 'ArrowUp'].includes(e.key)) {
            setOpen(true)
          }
          if (e.key === 'Escape' || e.key === 'Tab') setOpen(false)
          onKeyDown?.(e)
        }}
        onChange={(e) => {
          setOpen(false)
          onChange?.(e)
        }}
        onBlur={(e) => {
          setOpen(false)
          onBlur?.(e)
        }}
        className={cn(
          // `appearance-none` drops the platform arrow so ours is the only
          // one; the right padding keeps long labels from running under it.
          'h-11 w-full appearance-none rounded border border-white/[0.12] bg-black/40 pl-3 pr-9',
          'font-mono text-sm text-white',
          'transition-colors hover:border-white/25 focus:border-white/40 focus:outline-none',
          'disabled:cursor-not-allowed disabled:opacity-40',
          className,
        )}
      >
        {children}
      </select>

      {/* Decorative: the select above owns the interaction, so this must never
          swallow a click or reach the accessibility tree. */}
      <motion.span
        aria-hidden
        animate={{ rotate: open ? 180 : 0 }}
        transition={{ type: 'spring', stiffness: 300, damping: 25 }}
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-white/40"
      >
        <ChevronDown className="h-4 w-4" />
      </motion.span>
    </div>
  )
}
