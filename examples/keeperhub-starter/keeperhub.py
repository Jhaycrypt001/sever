"""A minimal, correct KeeperHub execution client.

Every workaround in here exists because the obvious version of the code fails
in a way that is hard to diagnose. Each one is annotated with the issue it
corresponds to and what actually goes wrong without it.

This is not illustrative code. It is the client that sent

    https://basescan.org/tx/0x62204d6591a117404d295e959b746a0bf10e812b4973bf8f92e427adee2cef2a

on Base mainnet, reduced to the smallest thing that still works.

    pip install httpx
    export KEEPERHUB_API_KEY=kh_...
    python revoke_example.py
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

DEFAULT_API_URL = "https://app.keeperhub.com"

#: Statuses that mean the execution is over, one way or the other.
_TERMINAL = {"completed", "failed"}


class KeeperHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class Execution:
    """The outcome of one contract call."""

    status: str
    transaction_hash: str | None = None
    transaction_link: str | None = None
    sponsored: bool = False
    raw: dict[str, Any] | None = None

    @property
    def succeeded(self) -> bool:
        """True only when a transaction was mined.

        Deliberately strict: a simulation is *not* a success, because nothing
        reached the chain. Collapsing the two is the single most expensive
        mistake you can make with this API — see `simulate()`.
        """
        return self.status == "completed" and bool(self.transaction_hash)


class KeeperHub:
    def __init__(
        self,
        api_key: str,
        api_url: str = DEFAULT_API_URL,
        client: httpx.Client | None = None,
        poll_interval: float = 2.0,
        poll_timeout: float = 90.0,
    ) -> None:
        if not api_key:
            raise ValueError("an API key is required")
        self._url = api_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    # ---------------------------------------------------------------- wallet

    def wallet_address(self) -> str:
        """The one wallet this key can execute as.

        Worth calling before you act on anything. KeeperHub provisions a single
        managed wallet per account and there is no way to add or delegate
        another, so *any* transaction you send comes from this address — not
        from whatever address your application is reasoning about.

        For a token allowance that distinction is everything: `approve(x, 0)`
        clears the allowance of the account that sends it. Sent on behalf of
        somebody else's wallet it is a real, gas-burning no-op that still
        returns a transaction hash you could easily mistake for proof.
        """
        response = self._client.get(f"{self._url}/api/user", headers=self._headers)
        response.raise_for_status()
        address = response.json().get("walletAddress")
        if not address:
            raise KeeperHubError("KeeperHub did not report a wallet address for this key")
        return str(address).lower()

    # ---------------------------------------------------------------- calls

    def simulate(
        self,
        contract_address: str,
        chain_id: int | str,
        function_name: str,
        args: list[Any],
    ) -> Execution:
        """Dry run. Never touches the chain.

        The response shape differs from a real execution: it comes back
        **synchronously**, with `status: "simulated"` and **no executionId** to
        poll. Code that assumes one shape for both falls through to its
        "missing executionId" error path and reports a perfectly good
        simulation as a failure.

        `wouldRevert` is the field that matters. `success: true` only means the
        simulation ran.
        """
        data = self._post(contract_address, chain_id, function_name, args, simulate=True)
        reverted = data.get("wouldRevert", True)
        return Execution(
            status="simulated" if data.get("success") and not reverted else "failed",
            sponsored=bool(data.get("sponsored")),
            raw=data,
        )

    def execute(
        self,
        contract_address: str,
        chain_id: int | str,
        function_name: str,
        args: list[Any],
    ) -> Execution:
        """Send a real transaction and wait for it to settle."""
        data = self._post(contract_address, chain_id, function_name, args, simulate=False)

        execution_id = data.get("executionId")
        if not execution_id:
            raise KeeperHubError(f"no executionId in the execute response: {data}")

        # ISSUE #1784: the POST response carries `status: "completed"` but no
        # transactionHash — that only ever appears on the status endpoint. If
        # you trust the POST you will believe you have no hash, or worse,
        # report success without one. Always poll.
        #
        # This also matters because sponsored/7702 executions are invisible to
        # the usual checks: the wallet's nonce does not move and its native
        # balance does not change, so "did it work?" cannot be answered by
        # looking at the wallet. The status endpoint is the answer.
        return self._poll(execution_id)

    # ---------------------------------------------------------------- internals

    def _post(
        self,
        contract_address: str,
        chain_id: int | str,
        function_name: str,
        args: list[Any],
        *,
        simulate: bool,
    ) -> dict[str, Any]:
        body = {
            "contractAddress": contract_address,
            # ISSUE #1841: chainId, functionArgs and gasLimitMultiplier must be
            # STRINGS, and functionArgs specifically a *stringified JSON array*
            # — not a JSON array. The schema does not say so. Passing the
            # natural types is rejected, and the rejection does not tell you
            # which field was wrong.
            "chainId": str(chain_id),
            "functionName": function_name,
            "functionArgs": json.dumps([str(a) for a in args]),
            "gasLimitMultiplier": "1.3",
            "simulate": simulate,
        }
        # ISSUE #1840: a reused Idempotency-Key replays a cached *failure*, not
        # only a cached success. Key it per attempt, never per action: keying
        # it per action means a retry after the precondition finally holds
        # still returns the original stale error, forever. The cached message
        # looks legitimate and nothing marks it as replayed.
        headers = {**self._headers, "Idempotency-Key": str(uuid4())}

        response = self._client.post(
            f"{self._url}/api/execute/contract-call", json=body, headers=headers
        )
        response.raise_for_status()
        return dict(response.json())

    def _poll(self, execution_id: str) -> Execution:
        deadline = time.monotonic() + self._poll_timeout
        while time.monotonic() < deadline:
            response = self._client.get(
                f"{self._url}/api/execute/{execution_id}/status", headers=self._headers
            )
            response.raise_for_status()
            data = dict(response.json())
            if data.get("status") in _TERMINAL:
                return Execution(
                    status=str(data.get("status")),
                    transaction_hash=data.get("transactionHash"),
                    transaction_link=data.get("transactionLink"),
                    sponsored=bool(data.get("sponsored")),
                    raw=data,
                )
            time.sleep(self._poll_interval)
        raise KeeperHubError(f"execution {execution_id} did not settle in {self._poll_timeout}s")
