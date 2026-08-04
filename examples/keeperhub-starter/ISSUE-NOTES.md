# Field notes for KeeperHub, ready to post

Drafted from a build that runs unattended against Base mainnet. Everything
below was observed on the live API, not inferred from docs — dates and
transaction hashes included so any of it can be checked.

**Not submitted.** These are drafts for a human to review, edit and post under
their own name. Read `CONTRIBUTING.md` in the KeeperHub repo first; #1841 in
particular is a small enough change that a PR may land faster than a comment.

---

## #1841 — string-typed fields (open)

Confirms the report, from a second independent implementation, and adds the
detail that cost the most time.

> Confirming this from a production integration on Base mainnet.
>
> The one that cost us longest was `function_args`. "Must be a string" reads
> like `"[a, b]"`, but it has to be a **JSON-encoded array**, and the element
> types matter too — a `uint256` passed as a Python `int` is rejected, so
> every element ends up stringified:
>
> ```python
> "functionArgs": json.dumps([spender, "0"])   # '["0x1e00…", "0"]'
> ```
>
> Passing the natural types fails without saying which field was wrong, which
> is what turns a five-minute integration into an afternoon.
>
> Of the three fixes proposed, **coercing internally** would have saved us
> entirely; marking the schema `"type": "string"` would have saved us if we
> had read it before writing code, which is the less likely order. A worked
> example in the tool description would have been enough on its own.

## #1840 — idempotency key replays cached failures (open)

Adds the agent-specific angle, which the original report does not cover.

> Hit this too, and worth adding: it is sharper for automated retries than for
> a human clicking again.
>
> The natural key for an agent is the *action* — "revoke this allowance" —
> because that is what you want to be idempotent. That is exactly the key that
> gets you stuck: the first attempt fails on a transient condition, the
> condition clears, and every subsequent attempt returns the original error.
> The agent then reports a permanent failure for something that would now
> succeed.
>
> Our workaround is a fresh UUID per **attempt**, which means we get no
> idempotency protection at all — an at-least-once retry can double-execute.
> We accepted that trade because for `approve(spender, 0)` a duplicate is a
> state no-op, but it would not be acceptable for a transfer, and right now
> there is no way to have both.
>
> Of the three suggestions, `cached: true` in the response is the one that
> would let a caller keep an action-scoped key *and* detect the replay.

## #1784 — no txHash in the execute response (closed)

Partly shipped. Worth reopening or filing a follow-up, because the headline
item is still true.

> Checking this against the current API (Base mainnet, 2026-08-04), two of the
> three asks have shipped and the first has not:
>
> **Shipped** — `GET /api/execute/{id}/status` now returns `transactionLink`
> alongside `transactionHash`, and a `sponsored: true` flag. The flag is more
> useful than it sounds: it is the only machine-readable confirmation that the
> wallet's own balance and nonce will not move, which is the exact confusion
> this issue opened about.
>
> **Not shipped** — `POST /api/execute/contract-call` still answers
> `{"executionId": "…", "status": "completed"}` with no hash and no `Location`
> header, even when the execution has already completed by the time it
> responds. A first-time integrator still has to discover the status endpoint
> to answer "did it work?".
>
> Concretely, from today:
>
> ```
> POST /api/execute/contract-call
> → {"executionId":"7ehe18h86m6wa87fs04mr","status":"completed"}
>
> GET /api/execute/7ehe18h86m6wa87fs04mr/status
> → {"status":"completed",
>    "transactionHash":"0xb283c3c6…82d35c",
>    "transactionLink":"https://basescan.org/tx/0xb283c3c6…82d35c",
>    "sponsored":true, …}
> ```
>
> Since `status` is already `completed` in the POST response, the hash appears
> to be known at that point.

---

## Also worth reporting (no issue yet)

### A simulation returns a different shape from an execution

Undocumented, and it sends integrators down a wrong path.

> `simulate: true` returns **synchronously** with `status: "simulated"` and
> **no `executionId`**:
>
> ```json
> {"success": true, "status": "simulated", "from": "0xe13e…",
>  "gasEstimate": "46641", "wouldRevert": false}
> ```
>
> A real execution returns `{"executionId": …}` to poll. Code written for the
> execute shape falls through to its "missing executionId" error branch and
> reports a working simulation as a failure — which is how we first read it.
>
> Two suggestions: document the shape difference, and consider whether
> `success` is the right name. `success: true` with `wouldRevert: true` means
> "the simulation ran and the call would fail", and the obvious reading of
> `success` is the opposite.

### The docs say to fund the wallet; execution is sponsored

> `docs.keeperhub.com` says "Fund your wallet with ETH on your target
> network". Our Base wallet has held **0 ETH** throughout and every execution
> has succeeded, with `"sponsored": true` in the response and the transaction
> sent by a relayer.
>
> We nearly removed a truthful "gas is sponsored" claim from our own product
> on the strength of that sentence. If sponsorship has conditions or limits,
> those are worth stating; if it is unconditional, the funding instruction is
> now misleading.
