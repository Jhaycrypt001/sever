# KeeperHub starter: a contract call that actually lands

A minimal client for KeeperHub's direct execution API, plus a worked example
that revokes an ERC-20 allowance and then **proves it** by reading the chain.

Every non-obvious line is here because the obvious version fails in a way that
is hard to diagnose. This is not illustrative code — it is the client that sent
[`0x62204d65…2cef2a`](https://basescan.org/tx/0x62204d6591a117404d295e959b746a0bf10e812b4973bf8f92e427adee2cef2a)
on Base mainnet, cut down to the smallest thing that still works.

```sh
pip install httpx
export KEEPERHUB_API_KEY=kh_...

# revoke <chain> <token> <spender>
python revoke_example.py 8453 0x4200000000000000000000000000000000000006 0x1e0049783f008a0085193e00003d00cd54003c71
```

```
executing as 0xe13ed979…f304bf
allowance before : 115792089237316195423570985008687907853269984665640564039457584007913129639935
simulation       : simulated
execution        : completed sponsored=True
transaction      : https://basescan.org/tx/0x62204d65…
allowance after  : 0
verified: allowance is zero on-chain
```

## The four things that will cost you an afternoon

### 1. Three fields must be strings, and one of them a stringified array

[#1841](https://github.com/KeeperHub/keeperhub/issues/1841). `chainId`,
`functionArgs` and `gasLimitMultiplier` are all strings, and `functionArgs` is
a *JSON-encoded string containing an array* — not an array. The schema does not
say so, so the natural types are what you try first.

```python
"chainId": "8453",                        # not 8453
"functionArgs": '["0x1e00…", "0"]',       # not ["0x1e00…", "0"]
"gasLimitMultiplier": "1.3",              # not 1.3
```

### 2. A reused idempotency key replays a cached *failure*

[#1840](https://github.com/KeeperHub/keeperhub/issues/1840). Key it per
**attempt**, not per action. Keying per action feels right — it is what
idempotency keys are for — and it means a retry after the precondition finally
holds still returns the original stale error, forever. The cached message looks
legitimate and nothing marks it as replayed.

### 3. The execute response has no transaction hash

[#1784](https://github.com/KeeperHub/keeperhub/issues/1784). `POST` answers
`{executionId, status}`; `transactionHash` and `transactionLink` only ever
appear on `GET /api/execute/{id}/status`. Always poll.

This compounds with sponsorship: executions are relayed, so the wallet's nonce
does not move and its native balance does not change. "Did my transaction
work?" cannot be answered by looking at the wallet — only by the status
endpoint. Verified live: our wallet held **0 ETH on Base** and still executed,
with `"sponsored": true` in the response.

### 4. A simulation returns a different shape from an execution

Undocumented. `simulate: true` answers **synchronously**, with
`status: "simulated"` and **no `executionId`**. Code written for the execute
shape falls through to its "missing executionId" branch and reports a perfectly
good simulation as a failure.

Read `wouldRevert`, not `success` — `success: true` only means the simulation
ran.

## The one rule worth more than the four workarounds

**A simulation is not a revocation, and neither is a status field.**

`simulate: true` sends nothing. If your UI renders that as "revoked", you have
told someone a draining allowance is gone while it is still live and still
spendable — the most expensive lie this kind of tool can tell. Give it its own
state and its own wording.

The same goes for `status: "completed"`. That is the API's opinion about its
own work. The allowance is the fact, and it costs one `eth_call` to check:

```python
after = read_allowance(chain_id, token, owner, spender)
assert after == 0
```

`revoke_example.py` does this on every run and exits non-zero if the status
says one thing and the chain says another.

## One wallet, and it is the only one you can act for

`GET /api/user` returns `walletAddress` — the single managed wallet this key
executes as. There is no endpoint to add or delegate another.

That matters more than it looks for allowances: `approve(spender, 0)` clears
the allowance of **whoever sends it**. Run it on behalf of an address you do
not control and it is a real, gas-burning no-op that still returns a
transaction hash — which is indistinguishable, in a log or a UI, from having
worked. Compare before you execute:

```python
if kh.wallet_address() != scanned_wallet.lower():
    return  # nothing to do here, and nothing honest to report
```

## Files

| | |
|---|---|
| `keeperhub.py` | The client. ~120 lines, no dependencies beyond `httpx`. |
| `revoke_example.py` | End to end: read wallet → simulate → execute → poll → verify on-chain. |
| `test_keeperhub.py` | `pytest -q` (needs `respx`). No network, no key. |

Extracted from [Approval Firewall](../../README.md), where the same adapter
runs unattended against live mainnet.
