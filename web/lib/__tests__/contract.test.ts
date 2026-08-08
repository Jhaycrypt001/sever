// Consumer side of the cross-language contract (ADR-049).
//
// The fixtures in `contracts/` are the shared definition of the wire shape;
// the Rust side asserts it serializes exactly these, and this asserts the
// console can parse exactly these. A field renamed on one side without the
// other fails here rather than in a browser at demo time.
//
// This is the check that would have caught the RevocationStatus serde bug:
// Rust briefly emitted "notattempted" where the schema says "not_attempted".

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import {
  approvalFindingSchema,
  keeperHubKeySchema,
  recurringScanSchema,
  revocationStatusSchema,
  scanJobDetailSchema,
} from '../api'

const HERE = dirname(fileURLToPath(import.meta.url))
const CONTRACTS = join(HERE, '..', '..', '..', 'contracts')

function fixture(name: string): unknown {
  return JSON.parse(readFileSync(join(CONTRACTS, `${name}.json`), 'utf8'))
}

describe('public API contract', () => {
  it('parses the job detail fixture', () => {
    const detail = scanJobDetailSchema.parse(fixture('search-job-detail'))
    expect(detail.wallet_address).toMatch(/^0x[a-fA-F0-9]{40}$/)
    expect(detail.results).toHaveLength(2)
    expect(detail.results[0].tier).toBe('dangerous')
    expect(detail.results[0].revocation_status).toBe('revoked')
    expect(detail.steps.map((s) => s.kind)).toContain('revoke')
  })

  it('parses the recurring-scan fixture', () => {
    const recurring = recurringScanSchema.parse(fixture('recurring-search'))
    expect(recurring.interval_minutes).toBeGreaterThan(0)
  })

  it('parses the connected-KeeperHub-key fixture', () => {
    const key = keeperHubKeySchema.parse(fixture('keeperhub-key'))
    expect(key.wallet_address).toMatch(/^0x[a-fA-F0-9]{40}$/)
    expect(key.masked).toMatch(/^•+/)
  })

  it('has nowhere to put a KeeperHub key (ADR-076)', () => {
    // The point of the shape, not an incidental property: a backend that
    // started echoing the key back would land in browser devtools and error
    // trackers, so the console refuses to carry a field for it. zod strips
    // unknown keys, so this asserts the parsed object, not the input.
    const parsed = keeperHubKeySchema.parse({
      ...(fixture('keeperhub-key') as object),
      api_key: 'kh_leaked',
    })
    expect(JSON.stringify(parsed)).not.toContain('kh_leaked')
  })

  it('parses each finding in the results callback', () => {
    const body = fixture('results-callback') as { results: unknown[] }
    for (const result of body.results) {
      expect(() => approvalFindingSchema.parse(result)).not.toThrow()
    }
  })
})

describe('revocation status vocabulary', () => {
  // Snake_case, not lowercase: "notattempted" is what a `rename_all =
  // "lowercase"` on the Rust enum produces, and it silently fails every
  // ingestion.
  it('is exactly the five states the backend can emit', () => {
    expect(revocationStatusSchema.options).toEqual([
      'not_attempted',
      'pending',
      'simulated',
      'revoked',
      'failed',
    ])
  })

  it('rejects a status the console has no rendering for', () => {
    expect(() => revocationStatusSchema.parse('notattempted')).toThrow()
    expect(() => revocationStatusSchema.parse('unrevoked')).toThrow()
  })
})
