#!/usr/bin/env bash
# End-to-end smoke test (ADR-021) against the full compose stack running with
# AGENT_PROVIDERS=fake. Exercises the real user journey through the Next.js
# server, which proxies /api to the backend (ADR-061): register -> login ->
# scan a wallet -> worker classifies and revokes -> findings come back sorted
# most-dangerous-first.
#
# The fake approval source returns the whole ADR-058 risk cascade per chain:
# a malicious spender (DANGEROUS, auto-revoked), an unverified one (WATCH) and
# a verified one (SAFE).
#
# Usage: scripts/e2e-smoke.sh [BASE_URL]   (default: http://localhost:8080)
set -euo pipefail

BASE_URL="${1:-http://localhost:8080}"
EMAIL="e2e-$(date +%s)-$RANDOM@test.dev"
PASSWORD="e2e-s3cret-password"
WALLET="0x1234567890123456789012345678901234567890"
# The one wallet the fake policy pauses on (ADR-032). It has to be valid hex,
# because the API and the console both reject anything else.
ASK_WALLET="0x00000000000000000000000000000000000a5c00"

say() { printf '\n== %s\n' "$*"; }
fail() { printf 'E2E FAILED: %s\n' "$*" >&2; exit 1; }

json_get() { # json_get <json> <python-expr on data>
  python3 -c "import json,sys; data=json.loads(sys.argv[1]); print($2)" "$1"
}

auth() { curl -sf -H "authorization: Bearer $TOKEN" "$@"; }

# Polls a job until it reaches one of the given statuses; echoes the detail.
wait_for_status() { # wait_for_status <job_id> <status>[|<status>...]
  local job_id="$1" wanted="$2" detail status
  for _ in $(seq 1 40); do
    detail=$(auth "$BASE_URL/api/searches/$job_id") || fail "get scan $job_id"
    status=$(json_get "$detail" 'data["status"]')
    case "|$wanted|" in
      *"|$status|"*) printf '%s' "$detail"; return 0 ;;
    esac
    [ "$status" = "failed" ] \
      && fail "job $job_id failed: $(json_get "$detail" 'data["error"]')"
    sleep 2
  done
  fail "job $job_id never reached '$wanted' (stuck at '$status')"
}

say "waiting for the stack ($BASE_URL)"
for _ in $(seq 1 30); do
  if curl -sf "$BASE_URL/api/../healthz" -o /dev/null 2>/dev/null \
     || curl -sf "$BASE_URL" -o /dev/null 2>/dev/null; then
    break
  fi
  sleep 2
done
curl -sf "$BASE_URL" -o /dev/null || fail "web unreachable at $BASE_URL"

say "register $EMAIL"
curl -sf -X POST "$BASE_URL/api/auth/register" \
  -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" >/dev/null \
  || fail "register"

say "login"
LOGIN=$(curl -sf -X POST "$BASE_URL/api/auth/login" \
  -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}") || fail "login"
TOKEN=$(json_get "$LOGIN" 'data["access_token"]')
[ -n "$TOKEN" ] || fail "no access token in login response"

say "launch a report-only scan (workflow mode — read-only by design)"
LAUNCH=$(auth -X POST "$BASE_URL/api/searches" -H 'content-type: application/json' \
  -d "{\"wallet_address\":\"$WALLET\"}") || fail "launch scan"
JOB_ID=$(json_get "$LAUNCH" 'data["job_id"]')
say "job $JOB_ID accepted, polling until completion"
DETAIL=$(wait_for_status "$JOB_ID" "completed")

say "checking the findings (sorted most-dangerous-first, ADR-058)"
TIERS=$(json_get "$DETAIL" '",".join(r["tier"] for r in data["results"])')
case "$TIERS" in
  dangerous*safe) ;;
  *) fail "findings are not sorted most-dangerous-first: $TIERS" ;;
esac
SYMBOLS=$(json_get "$DETAIL" '",".join(r["token_symbol"] for r in data["results"])')
case "$SYMBOLS" in
  fake-dangerous*) ;;
  *) fail "unexpected finding order: $SYMBOLS" ;;
esac
BEHAVIOUR=$(json_get "$DETAIL" 'data["results"][0]["malicious_behavior"][0]')
[ "$BEHAVIOUR" = "phishing_activities" ] || fail "missing threat signal: $BEHAVIOUR"

# Workflow mode never writes to a chain (ADR-030/058). A report-only run that
# claims a revocation would be the single worst bug this product can have.
REVOKED=$(json_get "$DETAIL" 'sum(1 for r in data["results"] if r["revocation_status"] != "not_attempted")')
[ "$REVOKED" = "0" ] || fail "report-only run reported $REVOKED revocations"

say "launch an auto-revoke scan (agent mode, ADR-030/058)"
LAUNCH=$(auth -X POST "$BASE_URL/api/searches" -H 'content-type: application/json' \
  -d "{\"wallet_address\":\"$WALLET\",\"mode\":\"agent\"}") || fail "launch agent scan"
AGENT_JOB=$(json_get "$LAUNCH" 'data["job_id"]')
AGENT_DETAIL=$(wait_for_status "$AGENT_JOB" "completed")

MODE=$(json_get "$AGENT_DETAIL" 'data["mode"]')
[ "$MODE" = "agent" ] || fail "unexpected mode: $MODE"

# Every DANGEROUS finding is revoked, and each one carries its receipt. A
# `revoked` without a transaction hash is a claim with nothing behind it.
DANGEROUS=$(json_get "$AGENT_DETAIL" 'sum(1 for r in data["results"] if r["tier"] == "dangerous")')
[ "$DANGEROUS" -gt 0 ] || fail "no dangerous findings to revoke"
UNREVOKED=$(json_get "$AGENT_DETAIL" 'sum(1 for r in data["results"] if r["tier"] == "dangerous" and r["revocation_status"] != "revoked")')
[ "$UNREVOKED" = "0" ] || fail "$UNREVOKED dangerous approvals were left live"
NO_RECEIPT=$(json_get "$AGENT_DETAIL" 'sum(1 for r in data["results"] if r["revocation_status"] == "revoked" and not r["revocation_tx_hash"])')
[ "$NO_RECEIPT" = "0" ] || fail "$NO_RECEIPT revocations have no transaction hash"

# Nothing below DANGEROUS is ever touched (ADR-058).
OVERREACH=$(json_get "$AGENT_DETAIL" 'sum(1 for r in data["results"] if r["tier"] != "dangerous" and r["revocation_status"] != "not_attempted")')
[ "$OVERREACH" = "0" ] || fail "$OVERREACH non-dangerous approvals were acted on"

say "checking the agent decision journal"
STEP_KINDS=$(json_get "$AGENT_DETAIL" '",".join(s["kind"] for s in data["steps"])')
# Fake policy (ADR-030/058): scan Ethereum -> scan Base -> finish, then one
# revoke step per auto-revoked finding.
case "$STEP_KINDS" in
  "scan,scan,finish,revoke"*) ;;
  *) fail "unexpected steps: $STEP_KINDS" ;;
esac

say "checking spend accounting (ADR-038) — fakes count calls, cost stays \$0"
COST=$(json_get "$AGENT_DETAIL" 'data["usage"]["cost_usd"]')
[ "$COST" = "0.0" ] || fail "keyless run should cost nothing, got $COST"

say "launch a scan that pauses for clarification (HITL, ADR-032)"
HITL=$(auth -X POST "$BASE_URL/api/searches" -H 'content-type: application/json' \
  -d "{\"wallet_address\":\"$ASK_WALLET\",\"mode\":\"agent\"}") || fail "launch HITL scan"
HITL_ID=$(json_get "$HITL" 'data["job_id"]')
HITL_DETAIL=$(wait_for_status "$HITL_ID" "awaiting_input")
QUESTION=$(json_get "$HITL_DETAIL" 'data["question"]')
case "$QUESTION" in
  "Which chains should I scan"*) ;;
  *) fail "unexpected question: $QUESTION" ;;
esac

say "answer the clarification and wait for completion"
auth -X POST "$BASE_URL/api/searches/$HITL_ID/answer" -H 'content-type: application/json' \
  -d '{"answer":"every supported chain"}' >/dev/null || fail "answer clarification"
HITL_DETAIL=$(wait_for_status "$HITL_ID" "completed")
[ "$(json_get "$HITL_DETAIL" 'data["answer"]')" = "every supported chain" ] \
  || fail "answer not stored"
[ "$(json_get "$HITL_DETAIL" 'len(data["results"]) > 0')" = "True" ] \
  || fail "resumed run produced no findings"

say "watch a wallet (ADR-033) — the scheduler launches the first run"
REC=$(auth -X POST "$BASE_URL/api/recurring" -H 'content-type: application/json' \
  -d "{\"wallet_address\":\"$WALLET\",\"mode\":\"agent\",\"interval_minutes\":1}") \
  || fail "create recurring"
REC_ID=$(json_get "$REC" 'data["id"]')

completed_runs() { # -> newline-separated job ids, oldest first
  LIST=$(auth "$BASE_URL/api/searches") || fail "list scans"
  json_get "$LIST" "'\n'.join(j['id'] for j in reversed(data) if j.get('recurring_search_id') == '$REC_ID' and j['status'] == 'completed')"
}

# First run: due immediately; the e2e stack ticks every SCHEDULER_TICK_SECONDS=5.
RUN1_ID=""
for _ in $(seq 1 30); do
  RUN1_ID=$(completed_runs | sed -n '1p')
  [ -n "$RUN1_ID" ] && break
  sleep 2
done
[ -n "$RUN1_ID" ] || fail "the scheduler never launched the first recurring run"
RUN1=$(auth "$BASE_URL/api/searches/$RUN1_ID")
[ "$(json_get "$RUN1" 'all(r["is_new"] for r in data["results"])')" = "True" ] \
  || fail "first recurring run: every approval should be new"
[ "$(json_get "$RUN1" 'data["steps"][-1]["kind"]')" = "report" ] || fail "missing report step"

say "wait for the second run — the memory flags everything as already seen"
RUN2_ID=""
for _ in $(seq 1 60); do
  RUN2_ID=$(completed_runs | sed -n '2p')
  [ -n "$RUN2_ID" ] && break
  sleep 2
done
[ -n "$RUN2_ID" ] || fail "the scheduler never launched the second recurring run"
RUN2=$(auth "$BASE_URL/api/searches/$RUN2_ID")
[ "$(json_get "$RUN2" 'any(r["is_new"] for r in data["results"])')" = "False" ] \
  || fail "second recurring run: nothing should be new"
REPORT=$(json_get "$RUN2" 'data["steps"][-1]["reason"]')
[ "$REPORT" = "Nothing new since the last scan" ] || fail "unexpected report: $REPORT"

auth -X DELETE "$BASE_URL/api/recurring/$REC_ID" >/dev/null || fail "delete recurring"

say "E2E OK — tiers=[$TIERS]; $DANGEROUS revoked with receipts; report-only stayed read-only; HITL answered; recurring delta verified"
