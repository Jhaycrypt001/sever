/**
 * Chain metadata for display only.
 *
 * The backend serves `chain_id` as an opaque string and never a name, because
 * a name is presentation. An unknown id renders as `chain <id>` rather than
 * being dropped — a finding on a chain this table has not heard of is still a
 * finding, and hiding it would be the one failure mode that matters.
 *
 * Defaults match `AGENT_SCAN_CHAIN_IDS` (ADR-058), plus Sepolia for the demo.
 */
type Chain = { name: string; explorer: string }

const CHAINS: Record<string, Chain> = {
  '1': { name: 'Ethereum', explorer: 'https://etherscan.io' },
  '10': { name: 'Optimism', explorer: 'https://optimistic.etherscan.io' },
  '56': { name: 'BNB Chain', explorer: 'https://bscscan.com' },
  '137': { name: 'Polygon', explorer: 'https://polygonscan.com' },
  '8453': { name: 'Base', explorer: 'https://basescan.org' },
  '42161': { name: 'Arbitrum', explorer: 'https://arbiscan.io' },
  '11155111': { name: 'Sepolia', explorer: 'https://sepolia.etherscan.io' },
}

export function chainName(chainId: string): string {
  return CHAINS[chainId]?.name ?? `chain ${chainId}`
}

/** Block-explorer link for a transaction, or null on an unmapped chain. */
export function txUrl(chainId: string, hash: string): string | null {
  const chain = CHAINS[chainId]
  return chain ? `${chain.explorer}/tx/${hash}` : null
}

/** Block-explorer link for an address, or null on an unmapped chain. */
export function addressUrl(chainId: string, address: string): string | null {
  const chain = CHAINS[chainId]
  return chain ? `${chain.explorer}/address/${address}` : null
}

/** `0x1234…abcd` — enough to recognise an address without wrapping a table cell. */
export function truncateAddress(address: string, lead = 6, tail = 4): string {
  if (address.length <= lead + tail + 1) return address
  return `${address.slice(0, lead)}…${address.slice(-tail)}`
}
