"""GoPlus Security adapter (ADR-058): the live `ApprovalSource`.

One call to GoPlus's `token_approval_security` v2 endpoint returns, per
chain, every outstanding ERC-20 approval for a wallet *with* the risk
signals already attached — no separate lookup per spender. Verified live
against the public API (keyless, rate-limited without `GOPLUS_API_KEY`):

    GET https://api.gopluslabs.io/api/v2/token_approval_security/{chain_id}?addresses={wallet}

The response nests one entry per approved token, each with an
`approved_list` of spenders. GoPlus flags malice at *two* levels that this
adapter must combine into the single `malicious_address` signal
`classify_risk` reads: the token itself (`malicious_address`/
`malicious_behavior` on the token entry — e.g. a scam token impersonating a
real one) and the spender contract (`address_info.malicious_behavior`/
`doubt_list` — e.g. a compromised or drainer contract). Either one is
dangerous: approving a scam token still burns gas/exposes the wallet to the
spender, and a malicious spender is dangerous regardless of the token.
"""

import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from aiagent.domain.models import RawApproval, flag_state
from aiagent.domain.usage import UsageMeter

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.gopluslabs.io/api/v2/token_approval_security"

#: "partial data obtained" — GoPlus is still indexing the address. Transient
#: (ADR-066), unlike 2018, which is a settled "no such chain".
_CODE_PARTIAL = 2

#: Returned both when a chain is genuinely not served *and* when a served
#: chain is being throttled — the anonymous tier answers the two identically
#: (ADR-074). Retried, because on the three chains we scan the first reading
#: is wrong: Ethereum answered 2029 twice in a row and then returned a full
#: approval list seconds later, unchanged request.
_CODE_UNAVAILABLE = 2029

#: Codes worth trying again. Anything else is a settled answer, and retrying
#: it only makes every scan slower.
_RETRYABLE = frozenset({_CODE_PARTIAL, _CODE_UNAVAILABLE})


def _spender_info(approval: dict[str, Any]) -> dict[str, Any]:
    info = approval.get("address_info")
    return info if isinstance(info, dict) else {}


def _behavior_list(value: Any) -> list[str]:
    """GoPlus reports behaviours as a list of tags; anything else (a bare
    string, null) is normalized rather than trusted, so a shape change cannot
    turn into a bogus risk signal downstream."""
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _to_raw_approval(chain_id: str, token: dict[str, Any], approval: dict[str, Any]) -> RawApproval:
    spender = _spender_info(approval)
    spender_behavior = _behavior_list(spender.get("malicious_behavior"))
    token_behavior = _behavior_list(token.get("malicious_behavior"))
    # Raw flags are passed through untouched; `classify_risk`'s `flag_state`
    # owns the coercion, so a provider switching 0/1 ints to "0"/"1" strings
    # cannot silently invert a decision that spends real gas (ADR-058).
    is_malicious = (
        bool(spender_behavior)
        or bool(token_behavior)
        or flag_state(spender.get("doubt_list")) is True
        or flag_state(token.get("malicious_address")) is True
    )
    combined_behavior = list(dict.fromkeys([*spender_behavior, *token_behavior]))
    approved_time = approval.get("approved_time")
    return RawApproval(
        chain_id=chain_id,
        token_address=str(token.get("token_address", "")),
        token_symbol=str(token.get("token_symbol") or token.get("token_name") or "UNKNOWN"),
        spender_address=str(approval.get("approved_contract", "")),
        approved_amount=str(approval.get("approved_amount", "0")),
        approved_at=(datetime.fromtimestamp(int(approved_time), tz=UTC) if approved_time else None),
        approval_tx_hash=approval.get("hash") or approval.get("initial_approval_hash"),
        spender_name=spender.get("contract_name") or spender.get("tag"),
        raw={
            # Normalized for `classify_risk` — the keys it reads.
            "malicious_address": is_malicious,
            "is_open_source": spender.get("is_open_source"),
            "trust_list": spender.get("trust_list"),
            # Carried through for the explanation/journal, not risk logic.
            "malicious_behavior": combined_behavior,
        },
    )


class GoPlusApprovalSource:
    """Live adapter — never exercised in CI (ADR-012); the network call
    itself is the one thing a unit test cannot verify. `RUN_LIVE_TESTS=1`
    covers the real endpoint shape (`tests/test_live_providers.py`)."""

    def __init__(
        self,
        meter: UsageMeter | None = None,
        client: httpx.Client | None = None,
        api_key: str = "",
        timeout: float = 15.0,
        partial_retries: int = 3,
        partial_retry_delay: float = 1.5,
    ) -> None:
        self._meter = meter
        self._client = client or httpx.Client(timeout=timeout)
        self._api_key = api_key
        # Retries for `code 2` only (ADR-066). Linear backoff: indexing a cold
        # address takes seconds, not minutes, and the worker holds a Celery
        # slot while it waits.
        self._partial_retries = partial_retries
        self._partial_retry_delay = partial_retry_delay

    def fetch_approvals(self, wallet_address: str, chain_id: str) -> list[RawApproval]:
        if self._meter is not None:
            self._meter.record_search()
        headers = {"Authorization": self._api_key} if self._api_key else {}

        payload: dict[str, Any] = {}
        for attempt in range(self._partial_retries + 1):
            response = self._client.get(
                f"{_BASE_URL}/{chain_id}",
                params={"addresses": wallet_address},
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
            code = payload.get("code")
            if code not in _RETRYABLE:
                break
            # ADR-066: `code 2` means GoPlus is still indexing this address and
            # what it has is incomplete. It clears on its own — confirmed live:
            # the first call for a cold address returns 2, a follow-up seconds
            # later returns 1 with the full set. Treating it as a hard error
            # threw away a whole chain on the *first* scan of any wallet, which
            # is the scan every new user runs.
            #
            # ADR-074: `2029` is retried for the opposite reason — not because
            # the address is cold but because the *caller* is throttled, and
            # the anonymous tier reports that with the same code it uses for a
            # chain it does not serve. On an unsupported chain the retries are
            # wasted but bounded; on a supported one they are the difference
            # between a report covering Ethereum and one that silently does
            # not.
            if attempt < self._partial_retries:
                logger.info(
                    "GoPlus returned code %s for chain %s, retrying (%d/%d)",
                    code,
                    chain_id,
                    attempt + 1,
                    self._partial_retries,
                )
                time.sleep(self._partial_retry_delay * (attempt + 1))

        if payload.get("code") != 1:
            # `.get(key, default)` returns None when the key exists *and* is
            # null, which GoPlus does on some error codes — that produced the
            # useless "GoPlus error: None". `or` covers both missing and null,
            # and the code is carried too: it is the only part an operator can
            # look up in their docs.
            message = payload.get("message") or "no message"
            # 2029 survives the retries either because the chain really is not
            # served or because the throttling outlasted them, and the response
            # cannot tell the two apart (ADR-074). Say so, rather than leaving
            # an operator to conclude the chain is unsupported when their scan
            # was simply rate-limited.
            if payload.get("code") == _CODE_UNAVAILABLE:
                message = (
                    f"{message} - chain not served, or the caller is rate-limited; "
                    "these are indistinguishable on the anonymous tier"
                )
            # Partial data is never merged in as if it were the whole picture:
            # an incomplete approval list rendered as a finished scan is a
            # coverage lie (ADR-059/064). The chain is reported unscanned.
            raise RuntimeError(f"GoPlus error (code {payload.get('code')}): {message}")
        tokens = payload.get("result") or []
        approvals: list[RawApproval] = []
        for token in tokens:
            for approval in token.get("approved_list") or []:
                approvals.append(_to_raw_approval(chain_id, token, approval))
        return approvals
