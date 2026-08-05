/**
 * Verifiable facts about this project — the single source of truth for every
 * claim the marketing site makes.
 *
 * Rule for this file: if a value cannot be checked by a stranger with a block
 * explorer or the public repository, it does not belong here, and if it is not
 * here it does not belong on the page. The whole product is a promise that we
 * only act on verified signals; a landing page full of invented customers
 * would undercut that promise before anyone reached the demo.
 *
 * Every value below was re-checked against the live system on 2026-08-04.
 */

/**
 * The flagship proof: a real `approve(spender, 0)` on **Base mainnet**,
 * decided by the classifier and broadcast with no human in the loop.
 *
 * The target was an unlimited WETH allowance to `Conduit`, a spender GoPlus
 * flags `honeypot_related_address` — a real contract, not a fixture.
 */
export const MAINNET_TX_HASH =
  '0x62204d6591a117404d295e959b746a0bf10e812b4973bf8f92e427adee2cef2a'

export const MAINNET_TX_URL = `https://basescan.org/tx/${MAINNET_TX_HASH}`

/** The spender that allowance belonged to, and why it was tiered dangerous. */
export const MAINNET_SPENDER = '0x1e0049783f008a0085193e00003d00cd54003c71'
export const MAINNET_SPENDER_NAME = 'Conduit'
export const MAINNET_SPENDER_FLAG = 'honeypot_related_address'
export const MAINNET_TOKEN = 'WETH'

/**
 * Read back from the chain after the revocation, with `eth_call`. This is the
 * number that proves it: the status field is the API's opinion, the allowance
 * is the fact.
 */
export const MAINNET_ALLOWANCE_AFTER = '0'

/** The earlier proof, on a testnet. Kept because it is still true. */
export const SEPOLIA_TX_HASH =
  '0xa3e2b054752adda3aa9696a6d5460ac40c9670e34044da276b62ee10d9822c28'

export const SEPOLIA_TX_URL = `https://sepolia.etherscan.io/tx/${SEPOLIA_TX_HASH}`

/** The KeeperHub-provisioned wallet the revocations executed as. */
export const EXECUTOR_WALLET = '0xe13ed979bc6b23d6d9608939051e9488e9f304bf'

/** Measured from the Sepolia execution's status payload. */
export const GAS_USED = '75,255'
export const EXECUTION_MS = '7,906'

/**
 * Chains the scanner actually covers.
 *
 * This is the complete list GoPlus's `token_approval_security` endpoint
 * serves — probed across 17 chain ids on 2026-08-04 (ADR-066). Arbitrum,
 * Polygon, Optimism, Avalanche and the rest answer `2018` or `2029` on every
 * attempt, and Sepolia has no approval data at all. Listing a chain we cannot
 * read would be the same lie as an invented customer logo.
 */
export const SUPPORTED_CHAINS = [
  { name: 'Ethereum', id: '1' },
  { name: 'BNB Chain', id: '56' },
  { name: 'Base', id: '8453' },
] as const

export const CHAIN_COUNT = SUPPORTED_CHAINS.length

/** Counted on 2026-08-04. Update these with the numbers, never by feel. */
export const TEST_COUNTS = {
  python: 237,
  rust: 201,
  postgres: 20,
  browser: 22,
} as const

export const ADR_COUNT = 71
export const MIGRATION_RANGE = '0001 – 0014'

export const REPO_URL = 'https://github.com/Jhaycrypt001/ai-agent-boilerplate'

/** Short form for display, e.g. 0xa3e2b0…822c28 */
export function truncateHash(hash: string, lead = 8, tail = 6): string {
  return `${hash.slice(0, lead)}…${hash.slice(-tail)}`
}
