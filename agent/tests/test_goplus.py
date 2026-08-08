"""GoPlus ApprovalSource adapter: maps the real API response shape (verified
live against https://api.gopluslabs.io) into RawApproval (ADR-058)."""

import hashlib
import json

import httpx
import pytest
import respx

from aiagent.adapters.goplus import GoPlusApprovalSource
from aiagent.domain.models import RiskTier, classify_risk

WALLET = "0x1234567890123456789012345678901234567890"


@respx.mock
def test_maps_a_token_with_multiple_spenders() -> None:
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 1,
                "message": "ok",
                "result": [
                    {
                        "token_address": "0xtoken",
                        "token_symbol": "USDC",
                        "is_open_source": 1,
                        "malicious_address": 0,
                        "malicious_behavior": [],
                        "approved_list": [
                            {
                                "approved_contract": "0xspender1",
                                "approved_amount": "Unlimited",
                                "approved_time": 1700000000,
                                "hash": "0xhash1",
                                "address_info": {
                                    "contract_name": "Router",
                                    "is_open_source": 1,
                                    "malicious_behavior": [],
                                    "doubt_list": 0,
                                    "trust_list": 1,
                                },
                            },
                            {
                                "approved_contract": "0xspender2",
                                "approved_amount": "100",
                                "approved_time": 1700000001,
                                "hash": "0xhash2",
                                "address_info": {
                                    "contract_name": None,
                                    "is_open_source": 0,
                                    "malicious_behavior": [],
                                    "doubt_list": 0,
                                    "trust_list": 0,
                                },
                            },
                        ],
                    }
                ],
            },
        )
    )
    source = GoPlusApprovalSource()

    approvals = source.fetch_approvals(WALLET, "1")

    assert len(approvals) == 2
    verified, unverified = approvals
    assert verified.spender_address == "0xspender1"
    assert verified.approved_amount == "Unlimited"
    assert verified.spender_name == "Router"
    assert verified.raw["malicious_address"] is False
    # Provider flags are passed through verbatim; `classify_risk`'s flag_state
    # owns the coercion, so the tier is what this assertion should pin.
    assert classify_risk(verified) is RiskTier.SAFE
    assert unverified.spender_address == "0xspender2"
    assert classify_risk(unverified) is RiskTier.WATCH


@respx.mock
def test_malicious_spender_behavior_flags_the_approval_as_malicious() -> None:
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 1,
                "message": "ok",
                "result": [
                    {
                        "token_address": "0xtoken",
                        "token_symbol": "USDC",
                        "malicious_address": 0,
                        "approved_list": [
                            {
                                "approved_contract": "0xbad",
                                "approved_amount": "Unlimited",
                                "address_info": {
                                    "malicious_behavior": ["phishing_activities"],
                                    "is_open_source": 1,
                                },
                            }
                        ],
                    }
                ],
            },
        )
    )
    approvals = GoPlusApprovalSource().fetch_approvals(WALLET, "1")

    assert approvals[0].raw["malicious_address"] is True
    assert "phishing_activities" in approvals[0].raw["malicious_behavior"]


@respx.mock
def test_malicious_token_flags_the_approval_even_with_a_clean_spender() -> None:
    # A scam token impersonating a real one is still dangerous to have
    # approved, regardless of the spender's own reputation (ADR-058).
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 1,
                "message": "ok",
                "result": [
                    {
                        "token_address": "0xscamtoken",
                        "token_symbol": "COW",
                        "malicious_address": 1,
                        "malicious_behavior": ["honeypot_related_address"],
                        "approved_list": [
                            {
                                "approved_contract": "0xclean",
                                "approved_amount": "Unlimited",
                                "address_info": {"malicious_behavior": [], "is_open_source": 1},
                            }
                        ],
                    }
                ],
            },
        )
    )
    approvals = GoPlusApprovalSource().fetch_approvals(WALLET, "1")

    assert approvals[0].raw["malicious_address"] is True


@respx.mock
def test_string_typed_provider_flags_do_not_cause_a_false_revocation() -> None:
    # GoPlus returns 0/1 ints today (verified live). If it ever switches to
    # "0"/"1" strings, bare truthiness would read every clean spender as
    # flagged and auto-revoke it with a real transaction. End-to-end guard.
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 1,
                "message": "ok",
                "result": [
                    {
                        "token_address": "0xtoken",
                        "token_symbol": "USDC",
                        "malicious_address": "0",
                        "approved_list": [
                            {
                                "approved_contract": "0xclean",
                                "approved_amount": "Unlimited",
                                "address_info": {
                                    "malicious_behavior": [],
                                    "doubt_list": "0",
                                    "is_open_source": "1",
                                },
                            }
                        ],
                    }
                ],
            },
        )
    )
    approvals = GoPlusApprovalSource().fetch_approvals(WALLET, "1")

    assert approvals[0].raw["malicious_address"] is False
    assert classify_risk(approvals[0]) is RiskTier.SAFE


@respx.mock
def test_no_approvals_returns_an_empty_list() -> None:
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 1, "message": "ok", "result": []})
    )
    assert GoPlusApprovalSource().fetch_approvals(WALLET, "1") == []


@respx.mock
def test_error_response_raises_instead_of_returning_empty() -> None:
    # A quota/key error must fail the job, not masquerade as zero findings —
    # else the agent reports a clean wallet that was never actually checked.
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 4029, "message": "rate limit exceeded"})
    )
    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        GoPlusApprovalSource().fetch_approvals(WALLET, "1")


@respx.mock
def test_a_null_message_does_not_become_the_string_none() -> None:
    # ADR-064: GoPlus returns `"message": null` on some error codes, and
    # `.get(key, default)` returns None when the key *exists* and is null —
    # which produced the useless "GoPlus error: None" in production logs.
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 4012, "message": None})
    )
    with pytest.raises(RuntimeError) as excinfo:
        GoPlusApprovalSource().fetch_approvals(WALLET, "1")

    assert "None" not in str(excinfo.value)
    # The code is the only part an operator can look up in GoPlus's docs.
    assert "4012" in str(excinfo.value)


@respx.mock
def test_a_missing_message_key_is_handled_too() -> None:
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 4012})
    )
    with pytest.raises(RuntimeError) as excinfo:
        GoPlusApprovalSource().fetch_approvals(WALLET, "1")

    assert "None" not in str(excinfo.value)
    assert "4012" in str(excinfo.value)


@respx.mock
def test_a_key_without_a_secret_stays_anonymous() -> None:
    """An App Key alone cannot be signed, so there is nothing to exchange.

    GoPlus ignores an unrecognised Authorization header rather than refusing
    the call, so sending the bare key looks like it works while leaving every
    request on the anonymous tier. Sending no header at all is the same tier
    and does not imply otherwise.
    """
    token = respx.post("https://api.gopluslabs.io/api/v1/token")
    route = respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 1, "message": "ok", "result": []})
    )

    GoPlusApprovalSource(api_key="app-key").fetch_approvals(WALLET, "1")

    assert not token.called
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
def test_a_key_and_secret_are_exchanged_for_an_access_token() -> None:
    token = respx.post("https://api.gopluslabs.io/api/v1/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 1,
                "message": "ok",
                "result": {"access_token": "tok-1", "expires_in": 3600},
            },
        )
    )
    route = respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 1, "message": "ok", "result": []})
    )

    GoPlusApprovalSource(api_key="app-key", app_secret="app-secret").fetch_approvals(WALLET, "1")

    # The access token authenticates the scan, never the App Key or the secret.
    assert route.calls.last.request.headers["authorization"] == "tok-1"
    body = json.loads(token.calls.last.request.content)
    assert body["app_key"] == "app-key"
    assert "app-secret" not in json.dumps(body), "the secret must never leave as plaintext"
    # sha1(app_key + time + app_secret), the scheme GoPlus documents.
    expected = hashlib.sha1(f"app-key{body['time']}app-secret".encode()).hexdigest()
    assert body["sign"] == expected


@respx.mock
def test_the_token_is_reused_across_scans() -> None:
    """One exchange, not one per chain: a three-chain scan would otherwise
    spend three of the rate-limited token calls the key exists to avoid."""
    token = respx.post("https://api.gopluslabs.io/api/v1/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "code": 1,
                "message": "ok",
                "result": {"access_token": "tok-1", "expires_in": 3600},
            },
        )
    )
    respx.get(url__regex=r".*/token_approval_security/\d+").mock(
        return_value=httpx.Response(200, json={"code": 1, "message": "ok", "result": []})
    )
    source = GoPlusApprovalSource(api_key="app-key", app_secret="app-secret")

    source.fetch_approvals(WALLET, "1")
    source.fetch_approvals(WALLET, "56")
    source.fetch_approvals(WALLET, "8453")

    assert token.call_count == 1


@respx.mock
def test_an_expired_token_is_refreshed_once_and_the_scan_retried() -> None:
    token = respx.post("https://api.gopluslabs.io/api/v1/token").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "code": 1,
                    "message": "ok",
                    "result": {"access_token": "stale", "expires_in": 3600},
                },
            ),
            httpx.Response(
                200,
                json={
                    "code": 1,
                    "message": "ok",
                    "result": {"access_token": "fresh", "expires_in": 3600},
                },
            ),
        ]
    )
    route = respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        side_effect=[
            httpx.Response(401, json={"code": 4011, "message": "token expired"}),
            httpx.Response(200, json={"code": 1, "message": "ok", "result": []}),
        ]
    )

    GoPlusApprovalSource(api_key="app-key", app_secret="app-secret").fetch_approvals(WALLET, "1")

    assert token.call_count == 2
    assert route.calls.last.request.headers["authorization"] == "fresh"


@respx.mock
def test_a_failed_exchange_degrades_to_anonymous_rather_than_failing_the_scan() -> None:
    """Losing the higher rate limit is worth a warning; losing the findings is
    not. An unauthenticated scan still reports real approvals (ADR-064 marks
    what it could not reach), so a broken token endpoint must not abort it."""
    respx.post("https://api.gopluslabs.io/api/v1/token").mock(
        return_value=httpx.Response(500, json={"code": 5000, "message": "boom"})
    )
    route = respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 1, "message": "ok", "result": []})
    )

    approvals = GoPlusApprovalSource(api_key="app-key", app_secret="app-secret").fetch_approvals(
        WALLET, "1"
    )

    assert approvals == []
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
def test_meters_one_call_per_fetch() -> None:
    from aiagent.domain.usage import UsageMeter

    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 1, "message": "ok", "result": []})
    )
    meter = UsageMeter()
    GoPlusApprovalSource(meter=meter).fetch_approvals(WALLET, "1")

    assert meter.snapshot().search_calls == 1


# ---------------------------------------------------------------- indexing retry (ADR-066)


@respx.mock
def test_partial_data_is_retried_and_succeeds_on_the_follow_up() -> None:
    """`code 2` means "still indexing", not "broken" (ADR-066).

    Confirmed live: the first request for an address GoPlus has not seen
    returns 2, and a follow-up seconds later returns 1 with the full set.
    Treating it as a hard error discarded an entire chain on the *first* scan
    of any wallet — the scan every new user runs.
    """
    route = respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        side_effect=[
            httpx.Response(200, json={"code": 2, "message": "partial data obtained"}),
            httpx.Response(
                200,
                json={
                    "code": 1,
                    "message": "ok",
                    "result": [
                        {
                            "token_address": "0xtoken",
                            "token_symbol": "TKN",
                            "approved_list": [
                                {"approved_contract": "0xspender", "approved_amount": "1"}
                            ],
                        }
                    ],
                },
            ),
        ]
    )

    approvals = GoPlusApprovalSource(partial_retry_delay=0).fetch_approvals(WALLET, "1")

    assert route.call_count == 2
    assert [a.spender_address for a in approvals] == ["0xspender"]


@respx.mock
def test_persistent_partial_data_is_reported_rather_than_half_delivered() -> None:
    # Still incomplete after the retries: the chain is reported unscanned
    # (ADR-064 marks it degraded) instead of passing a partial approval list
    # off as a finished sweep, which would be a coverage lie.
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 2, "message": "partial data obtained"})
    )

    with pytest.raises(RuntimeError, match="partial data obtained"):
        GoPlusApprovalSource(partial_retries=2, partial_retry_delay=0).fetch_approvals(WALLET, "1")


@respx.mock
def test_a_permanent_error_code_is_not_retried() -> None:
    # 2018 is a settled answer about the chain itself - there is no such chain,
    # and no number of retries invents one. Retrying it only slows every scan
    # down for nothing. (2029 is *not* in this category: see below.)
    route = respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/10").mock(
        return_value=httpx.Response(
            200, json={"code": 2018, "message": "Main chain does not exist!"}
        )
    )

    with pytest.raises(RuntimeError, match="2018"):
        GoPlusApprovalSource(partial_retries=3, partial_retry_delay=0).fetch_approvals(WALLET, "10")

    assert route.call_count == 1


@respx.mock
def test_a_throttled_chain_is_retried_and_succeeds_on_the_follow_up() -> None:
    """`2029` is not only "chain not served" — it is also the throttle (ADR-074).

    Observed live on 2026-08-05: Ethereum answered 2029 twice for a wallet,
    then returned a full approval list seconds later for the identical
    request. Read as a settled answer, that scan silently reported Ethereum
    as unscannable while the product's headline chain was working fine.
    """
    route = respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1")
    route.side_effect = [
        httpx.Response(200, json={"code": 2029, "message": None}),
        httpx.Response(
            200,
            json={
                "code": 1,
                "result": [
                    {
                        "token_address": "0xtoken",
                        "token_symbol": "WETH",
                        "approved_list": [
                            {
                                "approved_contract": "0xspender",
                                "approved_amount": "1000",
                                "address_info": {"is_open_source": 1},
                            }
                        ],
                    }
                ],
            },
        ),
    ]

    approvals = GoPlusApprovalSource(partial_retry_delay=0).fetch_approvals(WALLET, "1")

    assert route.call_count == 2
    assert [a.token_symbol for a in approvals] == ["WETH"]


@respx.mock
def test_a_chain_that_stays_unavailable_says_why_it_might_be() -> None:
    # After the retries the two causes are still indistinguishable, so the
    # error names both rather than letting an operator conclude the chain is
    # unsupported when they were simply rate-limited.
    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 2029, "message": None})
    )

    with pytest.raises(RuntimeError, match="rate-limited"):
        GoPlusApprovalSource(partial_retries=2, partial_retry_delay=0).fetch_approvals(WALLET, "1")
