"""GoPlus ApprovalSource adapter: maps the real API response shape (verified
live against https://api.gopluslabs.io) into RawApproval (ADR-058)."""

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
def test_api_key_is_sent_when_configured() -> None:
    route = respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 1, "message": "ok", "result": []})
    )
    GoPlusApprovalSource(api_key="secret-key").fetch_approvals(WALLET, "1")

    assert route.calls.last.request.headers["authorization"] == "secret-key"


@respx.mock
def test_meters_one_call_per_fetch() -> None:
    from aiagent.domain.usage import UsageMeter

    respx.get("https://api.gopluslabs.io/api/v2/token_approval_security/1").mock(
        return_value=httpx.Response(200, json={"code": 1, "message": "ok", "result": []})
    )
    meter = UsageMeter()
    GoPlusApprovalSource(meter=meter).fetch_approvals(WALLET, "1")

    assert meter.snapshot().search_calls == 1
