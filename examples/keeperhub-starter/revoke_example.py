"""Revoke an ERC-20 allowance through KeeperHub, end to end.

    export KEEPERHUB_API_KEY=kh_...
    python revoke_example.py 8453 0x4200...0006 0x1e00...3c71

Order of operations, and why:

1. Read the wallet this key executes as, and treat it as the only account
   whose allowances you can clear.
2. Simulate. Free, and it catches a reverting call before it costs anything.
3. Execute, then poll — the POST response has no transaction hash.
4. Verify the allowance is actually zero by reading the chain, rather than
   trusting the status field. This step is the point of the whole example.
"""

from __future__ import annotations

import os
import sys

import httpx

from keeperhub import KeeperHub

# `allowance(address,address)`
_ALLOWANCE_SELECTOR = "0xdd62ed3e"

_RPC = {
    "1": "https://eth.llamarpc.com",
    "56": "https://bsc-dataseed.binance.org",
    "8453": "https://mainnet.base.org",
}


def read_allowance(chain_id: str, token: str, owner: str, spender: str) -> int | None:
    """Current on-chain allowance, or None if we have no RPC for the chain."""
    rpc = _RPC.get(str(chain_id))
    if rpc is None:
        return None
    data = _ALLOWANCE_SELECTOR + owner[2:].rjust(64, "0") + spender[2:].rjust(64, "0")
    response = httpx.post(
        rpc,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_call",
            "params": [{"to": token, "data": data}, "latest"],
        },
        timeout=20,
    )
    response.raise_for_status()
    result = response.json().get("result")
    return int(result, 16) if result else None


def main() -> int:
    api_key = os.environ.get("KEEPERHUB_API_KEY", "")
    if not api_key:
        print("set KEEPERHUB_API_KEY", file=sys.stderr)
        return 2
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2

    chain_id, token, spender = sys.argv[1], sys.argv[2], sys.argv[3]
    kh = KeeperHub(api_key)

    owner = kh.wallet_address()
    print(f"executing as {owner}")

    before = read_allowance(chain_id, token, owner, spender)
    if before is not None:
        print(f"allowance before : {before}")
        if before == 0:
            print("nothing to revoke: the allowance is already zero")
            return 0

    dry = kh.simulate(token, chain_id, "approve", [spender, 0])
    print(f"simulation       : {dry.status}")
    if dry.status != "simulated":
        print(f"would revert, not sending: {dry.raw}", file=sys.stderr)
        return 1

    result = kh.execute(token, chain_id, "approve", [spender, 0])
    print(f"execution        : {result.status} sponsored={result.sponsored}")
    if not result.succeeded:
        print(f"failed: {result.raw}", file=sys.stderr)
        return 1
    print(f"transaction      : {result.transaction_link or result.transaction_hash}")

    # The only check that proves anything. A status field saying "completed"
    # is the API's opinion; the allowance is the fact.
    after = read_allowance(chain_id, token, owner, spender)
    if after is None:
        print("no RPC configured for this chain: allowance not independently verified")
        return 0
    print(f"allowance after  : {after}")
    if after != 0:
        print("REPORTED SUCCESS BUT THE ALLOWANCE IS STILL LIVE", file=sys.stderr)
        return 1
    print("verified: allowance is zero on-chain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
