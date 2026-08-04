"""Tests for the starter client. No network, no API key.

    pip install pytest respx httpx
    pytest -q

Each test pins one of the landmines the README describes, so a "simplification"
that reintroduces a bug fails here rather than on someone's mainnet wallet.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from keeperhub import KeeperHub, KeeperHubError

API = "https://app.keeperhub.com"
WALLET = "0xe13ed979bc6b23d6d9608939051e9488e9f304bf"
TOKEN = "0x4200000000000000000000000000000000000006"
SPENDER = "0x1e0049783f008a0085193e00003d00cd54003c71"


def client() -> KeeperHub:
    return KeeperHub("kh_test", api_url=API, poll_interval=0)


@respx.mock
def test_the_three_string_typed_fields_are_sent_as_strings() -> None:
    # #1841. Sending the natural JSON types is rejected, and the rejection does
    # not say which field was wrong.
    route = respx.post(f"{API}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "e1", "status": "pending"})
    )
    respx.get(f"{API}/api/execute/e1/status").mock(
        return_value=httpx.Response(200, json={"status": "completed", "transactionHash": "0xabc"})
    )

    client().execute(TOKEN, 8453, "approve", [SPENDER, 0])

    body = json.loads(route.calls.last.request.content)
    assert body["chainId"] == "8453"
    assert isinstance(body["gasLimitMultiplier"], str)
    # A *stringified* array, not an array.
    assert isinstance(body["functionArgs"], str)
    assert json.loads(body["functionArgs"]) == [SPENDER, "0"]


@respx.mock
def test_each_attempt_gets_its_own_idempotency_key() -> None:
    # #1840. A reused key replays a cached failure, so a retry after the
    # precondition finally holds can never recover.
    route = respx.post(f"{API}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "e1", "status": "pending"})
    )
    respx.get(f"{API}/api/execute/e1/status").mock(
        return_value=httpx.Response(200, json={"status": "completed", "transactionHash": "0xabc"})
    )

    kh = client()
    kh.execute(TOKEN, 8453, "approve", [SPENDER, 0])
    kh.execute(TOKEN, 8453, "approve", [SPENDER, 0])

    keys = [c.request.headers["idempotency-key"] for c in route.calls]
    assert len(keys) == 2 and keys[0] != keys[1]


@respx.mock
def test_the_transaction_hash_comes_from_the_status_endpoint() -> None:
    # #1784. The POST says "completed" and carries no hash; only the status
    # endpoint has one. Trusting the POST loses it.
    respx.post(f"{API}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"executionId": "e1", "status": "completed"})
    )
    respx.get(f"{API}/api/execute/e1/status").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "completed",
                "transactionHash": "0xabc",
                "transactionLink": "https://basescan.org/tx/0xabc",
                "sponsored": True,
            },
        )
    )

    result = client().execute(TOKEN, 8453, "approve", [SPENDER, 0])

    assert result.succeeded
    assert result.transaction_hash == "0xabc"
    assert result.sponsored is True


@respx.mock
def test_a_simulation_is_not_reported_as_a_sent_transaction() -> None:
    # The rule that matters most: nothing reached the chain, so the allowance
    # is still live and `succeeded` must stay False.
    respx.post(f"{API}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "status": "simulated", "wouldRevert": False},
        )
    )

    result = client().simulate(TOKEN, 8453, "approve", [SPENDER, 0])

    assert result.status == "simulated"
    assert result.succeeded is False
    assert result.transaction_hash is None


@respx.mock
def test_a_reverting_simulation_is_not_a_success() -> None:
    # `success: true` only means the simulation ran. `wouldRevert` is the
    # field that decides.
    respx.post(f"{API}/api/execute/contract-call").mock(
        return_value=httpx.Response(
            200,
            json={"success": True, "status": "simulated", "wouldRevert": True},
        )
    )

    assert client().simulate(TOKEN, 8453, "approve", [SPENDER, 0]).status == "failed"


@respx.mock
def test_a_missing_execution_id_is_an_error_not_a_silent_success() -> None:
    respx.post(f"{API}/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json={"status": "completed"})
    )

    with pytest.raises(KeeperHubError, match="no executionId"):
        client().execute(TOKEN, 8453, "approve", [SPENDER, 0])


@respx.mock
def test_the_wallet_address_is_read_from_the_api() -> None:
    respx.get(f"{API}/api/user").mock(
        return_value=httpx.Response(200, json={"walletAddress": WALLET.upper()})
    )
    # Lower-cased so a checksum-cased address compares equal to a stored one.
    assert client().wallet_address() == WALLET


@respx.mock
def test_a_key_with_no_wallet_fails_loudly() -> None:
    respx.get(f"{API}/api/user").mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(KeeperHubError, match="wallet address"):
        client().wallet_address()
