import type * as React from 'react'
import { cn } from '@/lib/utils'

/* -------------------------------------------------------------------------- */
/*  Mono label                                                                */
/* -------------------------------------------------------------------------- */

export function Label({
  className,
  children,
  ...props
}: React.ComponentProps<'span'>) {
  return (
    <span
      className={cn('label text-muted-foreground', className)}
      {...props}
    >
      {children}
    </span>
  )
}

/* -------------------------------------------------------------------------- */
/*  Section meta rule — three mono columns above a hairline                    */
/* -------------------------------------------------------------------------- */

export function MetaRule({
  left,
  center,
  right,
  dot = false,
  className,
}: {
  left?: React.ReactNode
  center?: React.ReactNode
  right?: React.ReactNode
  dot?: boolean
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-col gap-3 border-b border-border/60 pb-4 md:flex-row md:items-center',
        className,
      )}
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        {dot ? (
          <span
            aria-hidden="true"
            className="size-1.5 shrink-0 rounded-full bg-foreground/70"
          />
        ) : null}
        <Label className="truncate">{left}</Label>
      </div>
      <div className="flex flex-1 justify-start md:justify-center">
        <Label className="truncate">{center}</Label>
      </div>
      <div className="flex flex-1 justify-start md:justify-end">
        <Label className="truncate">{right}</Label>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/*  Two-tone display heading                                                  */
/* -------------------------------------------------------------------------- */

export function Display({
  lead,
  trail,
  className,
  as: Tag = 'h2',
}: {
  lead: React.ReactNode
  trail?: React.ReactNode
  className?: string
  as?: 'h1' | 'h2' | 'h3'
}) {
  return (
    <Tag className={cn('display text-balance text-foreground', className)}>
      {lead}
      {trail ? (
        <>
          {' '}
          <span className="text-muted-foreground">{trail}</span>
        </>
      ) : null}
    </Tag>
  )
}

/* -------------------------------------------------------------------------- */
/*  Glowing pill CTA                                                          */
/* -------------------------------------------------------------------------- */

export function GlowPill({
  children,
  className,
  href = '#',
  ...props
}: React.ComponentProps<'a'>) {
  return (
    <a
      href={href}
      className={cn(
        'group relative inline-flex shrink-0 items-center justify-center rounded-full',
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className="absolute -inset-[3px] rounded-full opacity-70 blur-[6px] transition-opacity duration-300 group-hover:opacity-100"
        style={{
          background:
            'conic-gradient(from 210deg, #6ee7ff, #a78bfa, #fb7185, #fbbf24, #34d399, #6ee7ff)',
        }}
      />
      <span className="relative flex items-center rounded-full border border-white/15 bg-[#0b0b0b] px-7 py-3.5 text-[0.9375rem] font-medium tracking-tight text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]">
        {children}
      </span>
    </a>
  )
}

/** The GlowPill treatment on a real <button> — for forms that actually submit. */
export function GlowButton({
  children,
  className,
  ...props
}: React.ComponentProps<'button'>) {
  return (
    <button
      className={cn(
        'group relative inline-flex shrink-0 items-center justify-center rounded-full',
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className="absolute -inset-[3px] rounded-full opacity-70 blur-[6px] transition-opacity duration-300 group-hover:opacity-100"
        style={{
          background:
            'conic-gradient(from 210deg, #6ee7ff, #a78bfa, #fb7185, #fbbf24, #34d399, #6ee7ff)',
        }}
      />
      <span className="relative flex items-center rounded-full border border-white/15 bg-[#0b0b0b] px-7 py-3.5 text-[0.9375rem] font-medium tracking-tight text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.14)]">
        {children}
      </span>
    </button>
  )
}

export function GhostLink({
  children,
  className,
  href = '#',
  ...props
}: React.ComponentProps<'a'>) {
  return (
    <a
      href={href}
      className={cn(
        'inline-flex shrink-0 items-center text-[0.9375rem] font-semibold tracking-tight text-foreground transition-opacity hover:opacity-60',
        className,
      )}
      {...props}
    >
      {children}
    </a>
  )
}

/* -------------------------------------------------------------------------- */
/*  Layout shell                                                              */
/* -------------------------------------------------------------------------- */

export function Shell({
  className,
  children,
  ...props
}: React.ComponentProps<'div'>) {
  return (
    <div
      className={cn('mx-auto w-full max-w-[1232px] px-6 md:px-8', className)}
      {...props}
    >
      {children}
    </div>
  )
}
