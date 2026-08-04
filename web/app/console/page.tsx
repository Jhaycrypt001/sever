import type { Metadata } from 'next'
import { ConsoleApp } from '@/components/console/console-app'

export const metadata: Metadata = {
  title: 'Console · Sever',
  description:
    'Scan a wallet for outstanding token approvals and watch the dangerous ones get revoked onchain.',
}

/**
 * `?address=0x…` pre-fills the launcher, so the scan box on the public page is
 * a real entry point rather than an anchor. Anything that is not a well-formed
 * address is dropped rather than echoed back into the form.
 */
export default async function ConsolePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}) {
  const { address } = await searchParams
  const candidate = Array.isArray(address) ? address[0] : address
  const initialAddress =
    candidate && /^0x[a-fA-F0-9]{40}$/.test(candidate) ? candidate : ''

  // No Shell here: the signed-out screen is full-bleed, and the workspace
  // applies its own. Wrapping both would box the auth plate into a column.
  return (
    <div className="dark min-h-screen bg-background text-foreground">
      <ConsoleApp initialAddress={initialAddress} />
    </div>
  )
}
