/**
 * Verifiable facts about this project — the single source of truth for every
 * claim the marketing site makes.
 *
 * Rule for this file: if a value cannot be checked by a stranger with a block
 * explorer or the public repository, it does not belong here, and if it is not
 * here it does not belong on the page. The whole product is a promise that we
 * only act on verified signals; a landing page full of invented customers
 * would undercut that promise before anyone reached the demo.
 */

/** A real `approve(spender, 0)` broadcast through KeeperHub on 2026-08-02. */
export const SEPOLIA_TX_HASH =
  '0xa3e2b054752adda3aa9696a6d5460ac40c9670e34044da276b62ee10d9822c28'

export const SEPOLIA_TX_URL = `https://sepolia.etherscan.io/tx/${SEPOLIA_TX_HASH}`

/** The KeeperHub-provisioned wallet that signed it. */
export const EXECUTOR_WALLET = '0xe13ed979bc6b23d6d9608939051e9488e9f304bf'

/** Measured from that execution's status payload. */
export const GAS_USED = '75,255'
export const EXECUTION_MS = '7,906'

/** Chains the scanner is configured for out of the box. */
export const SUPPORTED_CHAINS = [
  { name: 'Ethereum', id: '1' },
  { name: 'Base', id: '8453' },
  { name: 'Arbitrum', id: '42161' },
  { name: 'Polygon', id: '137' },
  { name: 'Sepolia', id: '11155111' },
] as const

export const REPO_URL = 'https://github.com/Jhaycrypt001/keeperhub_Agent'

/** Short form for display, e.g. 0xa3e2b0…822c28 */
export function truncateHash(hash: string, lead = 8, tail = 6): string {
  return `${hash.slice(0, lead)}…${hash.slice(-tail)}`
}
