// Typed client for the Rust backend (public API contracts, ARCHITECTURE.md §4).
//
// The zod schemas below are the single source of truth for the wire contract
// (ADR-049): the TS types are derived (`z.infer`) and responses are validated
// at runtime, so a backend drift surfaces as a clear error instead of a silent
// `undefined`. The same shapes are pinned by `contracts/*.json` and asserted on
// the Rust side too. Objects are deliberately non-strict (zod strips unknown
// keys): an additive backend field is ignored, not rejected, so an older
// console keeps working during a rolling deploy.

import { z } from 'zod'

export const jobStatusSchema = z.enum([
  'pending',
  'running',
  'awaiting_input',
  'completed',
  'failed',
])
export type JobStatus = z.infer<typeof jobStatusSchema>

// Workflow = fixed pipeline; agent = decision loop (ADR-030).
export const jobModeSchema = z.enum(['workflow', 'agent'])
export type JobMode = z.infer<typeof jobModeSchema>

/**
 * How dangerous an approval is (ADR-058). Assigned by the worker's
 * `classify_risk`, never by a model and never by this client — the console
 * only renders what the backend already decided.
 */
export const riskTierSchema = z.enum(['safe', 'watch', 'dangerous'])
export type RiskTier = z.infer<typeof riskTierSchema>

/**
 * What happened when a dangerous approval was sent for revocation (ADR-058).
 *
 * `simulated` is a dry run: the approval is still live onchain. Only `revoked`
 * means a transaction was broadcast and confirmed, and it always carries a
 * `revocation_tx_hash` to prove it (ADR-059).
 */
export const revocationStatusSchema = z.enum([
  'not_attempted',
  'pending',
  'simulated',
  'revoked',
  'failed',
])
export type RevocationStatus = z.infer<typeof revocationStatusSchema>

export const approvalFindingSchema = z.object({
  chain_id: z.string(),
  token_address: z.string(),
  token_symbol: z.string(),
  spender_address: z.string(),
  spender_name: z.string().nullable(),
  approved_amount: z.string(),
  tier: riskTierSchema,
  malicious_behavior: z.array(z.string()),
  explanation: z.string().nullable(),
  // False when a previous run of a recurring scan already saw it (ADR-033).
  is_new: z.boolean(),
  revocation_status: revocationStatusSchema,
  revocation_tx_hash: z.string().nullable(),
  raw: z.unknown(),
})
export type ApprovalFinding = z.infer<typeof approvalFindingSchema>

// Accumulated API spend of a run (ADR-038).
export const jobUsageSchema = z.object({
  llm_calls: z.number(),
  llm_input_tokens: z.number(),
  llm_output_tokens: z.number(),
  search_calls: z.number(),
  cost_usd: z.number(),
})
export type JobUsage = z.infer<typeof jobUsageSchema>

export const scanJobSchema = z.object({
  id: z.string(),
  wallet_address: z.string(),
  mode: jobModeSchema,
  status: jobStatusSchema,
  error: z.string().nullable(),
  // Clarification dialog (ADR-032): the agent's question and the user's answer.
  question: z.string().nullable(),
  answer: z.string().nullable(),
  // Set on scheduler-launched runs of a recurring scan (ADR-033).
  recurring_search_id: z.string().nullable(),
  usage: jobUsageSchema,
  created_at: z.string(),
  completed_at: z.string().nullable(),
})
export type ScanJob = z.infer<typeof scanJobSchema>

// A saved scan re-run on an interval by the backend scheduler (ADR-033).
export const recurringScanSchema = z.object({
  id: z.string(),
  wallet_address: z.string(),
  mode: jobModeSchema,
  interval_minutes: z.number(),
  // Digest target (ADR-036): notified when a run finds new approvals.
  webhook_url: z.string().nullable(),
  created_at: z.string(),
  last_run_at: z.string().nullable(),
})
export type RecurringScan = z.infer<typeof recurringScanSchema>

// One decision of the agent loop (ADR-030), shown in the live journal. `kind`
// stays an open string: newer agents may add step kinds before the console.
export const agentStepSchema = z.object({
  seq: z.number(),
  kind: z.string(),
  detail: z.string(),
  reason: z.string(),
  new_hits: z.number(),
})
export type AgentStep = z.infer<typeof agentStepSchema>

export const scanJobDetailSchema = scanJobSchema.extend({
  results: z.array(approvalFindingSchema),
  steps: z.array(agentStepSchema),
})
export type ScanJobDetail = z.infer<typeof scanJobDetailSchema>

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    /**
     * The parsed error body. Some failures carry more than a sentence: an
     * unverified sign-in (ADR-062) returns `code: "email_not_verified"`, which
     * is what tells the console to open the code screen rather than claim the
     * password was wrong. Kept as-is so callers read the fields they know.
     */
    public readonly body: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }

  /** Machine-readable discriminator, where the endpoint provides one. */
  get code(): string | undefined {
    return typeof this.body.code === 'string' ? this.body.code : undefined
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const response = await fetch(path, { ...options, headers })
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<
      string,
      unknown
    >
    const message =
      typeof body.error === 'string' ? body.error : response.statusText
    throw new ApiError(response.status, message, body)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export interface SSEEvent {
  event: string
  data: string
}

/**
 * Incremental parser for a text/event-stream body (ADR-026). Feed it chunks
 * (arbitrarily split); it returns the complete events found so far.
 */
export function createSSEParser(): (chunk: string) => SSEEvent[] {
  let buffer = ''
  return (chunk: string): SSEEvent[] => {
    buffer += chunk
    const events: SSEEvent[] = []
    let separator: number
    while ((separator = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, separator)
      buffer = buffer.slice(separator + 2)
      let event = 'message'
      const data: string[] = []
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data.push(line.slice(5).trim())
        // lines starting with ":" are keep-alive comments — ignored
      }
      if (data.length > 0) events.push({ event, data: data.join('\n') })
    }
    return events
  }
}

/**
 * Streams job updates over SSE. `EventSource` cannot send an Authorization
 * header, so this uses fetch + ReadableStream instead (ADR-026). Resolves when
 * the server closes the stream (terminal status); rejects on transport errors
 * so the caller can fall back to polling.
 */
async function streamScan(
  id: string,
  token: string,
  onUpdate: (job: ScanJobDetail) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/searches/${id}/events`, {
    headers: {
      Accept: 'text/event-stream',
      Authorization: `Bearer ${token}`,
    },
    signal,
  })
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, response.statusText)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  const parse = createSSEParser()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) return
    for (const event of parse(decoder.decode(value, { stream: true }))) {
      // Validate the streamed shape too (ADR-026/049): the SSE payload is the
      // same job detail as GET, so a drift is caught here as well.
      if (event.event === 'update') {
        onUpdate(scanJobDetailSchema.parse(JSON.parse(event.data)))
      }
    }
  }
}

export const api = {
  streamScan,

  /**
   * Creates the account and mails it a code (ADR-062). It does **not** sign
   * anyone in — `verifyEmail` does, once the code comes back.
   *
   * `verification_code` is only ever present when the backend is running
   * without a mail provider, which is development and the browser tests; in
   * production the field is absent and the code is in the inbox.
   */
  register: (email: string, password: string) =>
    request<{
      id: string
      email: string
      verification_required: boolean
      verification_code: string | null
    }>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  /**
   * Checks the password and mails a sign-in code (ADR-063). Returns **no
   * session** — every sign-in takes two factors, and `verifyEmail` finishes it.
   */
  login: (email: string, password: string) =>
    request<{ verification_code: string | null }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),

  /** Answers a code. This is what actually signs someone in. */
  verifyEmail: (email: string, code: string) =>
    request<{ access_token: string }>('/api/auth/verify', {
      method: 'POST',
      body: JSON.stringify({ email, code }),
    }),

  /**
   * Starts account recovery (ADR-063). Answers the same way whether or not
   * the address has an account — a forgot-password form that says "no such
   * user" is an account enumeration tool.
   */
  forgotPassword: (email: string) =>
    request<{ verification_code: string | null }>('/api/auth/password/forgot', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),

  /**
   * Sets a new password from a reset code and signs in. Every other session
   * for the account is revoked, which is the point for someone resetting
   * because they think a stranger is inside.
   */
  resetPassword: (email: string, code: string, password: string) =>
    request<{ access_token: string }>('/api/auth/password/reset', {
      method: 'POST',
      body: JSON.stringify({ email, code, password }),
    }),

  /**
   * Asks for a fresh code, superseding any outstanding one. Answers the same
   * way whether or not the address needs one, so it cannot be used to find out
   * who has an account.
   */
  resendVerification: (email: string) =>
    request<{ verification_code: string | null }>('/api/auth/verify/resend', {
      method: 'POST',
      body: JSON.stringify({ email }),
    }),

  // The refresh token travels in an HttpOnly cookie (ADR-008) — the browser
  // attaches it automatically on same-origin requests.
  refresh: () =>
    request<{ access_token: string }>('/api/auth/refresh', { method: 'POST' }),

  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),

  launchScan: (
    walletAddress: string,
    token: string,
    mode: JobMode = 'workflow',
  ) =>
    request<{ job_id: string }>(
      '/api/searches',
      {
        method: 'POST',
        body: JSON.stringify({ wallet_address: walletAddress, mode }),
      },
      token,
    ),

  listScans: async (token: string) =>
    z.array(scanJobSchema).parse(await request<unknown>('/api/searches', {}, token)),

  getScan: async (id: string, token: string) =>
    scanJobDetailSchema.parse(
      await request<unknown>(`/api/searches/${id}`, {}, token),
    ),

  // Answers the agent's clarification question (ADR-032): the job resumes.
  answerScan: (id: string, answer: string, token: string) =>
    request<void>(
      `/api/searches/${id}/answer`,
      { method: 'POST', body: JSON.stringify({ answer }) },
      token,
    ),

  // Recurring scans (ADR-033); the webhook receives digests (ADR-036).
  createRecurring: async (
    walletAddress: string,
    mode: JobMode,
    intervalMinutes: number,
    token: string,
    webhookUrl?: string,
  ) =>
    recurringScanSchema.parse(
      await request<unknown>(
        '/api/recurring',
        {
          method: 'POST',
          body: JSON.stringify({
            wallet_address: walletAddress,
            mode,
            interval_minutes: intervalMinutes,
            webhook_url: webhookUrl || null,
          }),
        },
        token,
      ),
    ),

  listRecurring: async (token: string) =>
    z
      .array(recurringScanSchema)
      .parse(await request<unknown>('/api/recurring', {}, token)),

  deleteRecurring: (id: string, token: string) =>
    request<void>(`/api/recurring/${id}`, { method: 'DELETE' }, token),
}
