'use client'

import { Eye, EyeOff } from 'lucide-react'
import { useId, useState } from 'react'
import { cn } from '@/lib/utils'

export interface PasswordInputProps
  extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string
}

/**
 * Password field with a reveal toggle.
 *
 * Worth the extra control on this product specifically: people who manage
 * wallets use long generated passwords, and mistyping one into an invisible
 * box is the most common reason a sign-in fails twice in a row.
 */
export function PasswordInput({
  className,
  label,
  id: providedId,
  ...props
}: PasswordInputProps) {
  const generatedId = useId()
  const id = providedId ?? generatedId
  const [visible, setVisible] = useState(false)

  return (
    <div className="flex w-full flex-col gap-1.5">
      {label ? (
        <label
          htmlFor={id}
          className="font-mono text-[10px] uppercase tracking-[0.08em] text-white/40"
        >
          {label}
        </label>
      ) : null}
      <div className="relative">
        <input
          id={id}
          type={visible ? 'text' : 'password'}
          className={cn(
            'h-11 w-full rounded border border-white/[0.12] bg-black/40 pe-11 ps-3 font-mono text-sm text-white',
            'placeholder:text-white/25 focus:border-white/40 focus:outline-none',
            className,
          )}
          {...props}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? 'Hide password' : 'Show password'}
          aria-pressed={visible}
          className="absolute inset-y-0 end-0 flex w-11 items-center justify-center text-white/40 transition-colors hover:text-white focus-visible:text-white focus-visible:outline-none"
        >
          {visible ? (
            <EyeOff className="size-4" aria-hidden="true" />
          ) : (
            <Eye className="size-4" aria-hidden="true" />
          )}
        </button>
      </div>
    </div>
  )
}
