#!/usr/bin/env bash
#
# Prove that several processes sharing one inventory never pay out the same notes.
#
# Usage:  ./scripts/shared-inventory-test.sh [customers]      (default: 10)
#
# The ATM holds 8 notes of ₹500. The customers start at the same moment, each in its own
# python3 process, and each asks for ₹1000 (2 notes), so only 4 of them can be paid.
#
#   1. Without a state file every process is its own ATM: all of them are paid, from notes
#      that do not exist. This is the problem.
#   2. With one shared state file (--state-file) the file lock makes them take turns:
#      exactly 4 are paid, the rest get "Cannot dispense requested amount", and 0 notes remain.

set -euo pipefail
cd "$(dirname "$0")/.."

CUSTOMERS="${1:-10}"
NOTES="500:8"
CAN_BE_PAID=4
CLI="atm-dispenser/src/cli.py"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT
failures=0

step() { echo; echo "==> $*"; }
pass() { echo "    PASS  $*"; }
fail() { echo "    FAIL  $*"; failures=$((failures + 1)); }


# Start one customer in the background. $1 = customer number, $2 = state file ("" for none).
start_customer() {
  local output="${WORK}/customer-$1.txt"
  if [[ -n "$2" ]]; then
    python3 "${CLI}" --inventory "${NOTES}" --state-file "$2" 1000 > "${output}" 2>&1 &
  else
    python3 "${CLI}" --inventory "${NOTES}" 1000 > "${output}" 2>&1 &
  fi
}

# Start every customer at once, wait for all of them, and print how many were paid.
withdraw_at_the_same_time() {
  rm -f "${WORK}"/customer-*.txt
  for customer in $(seq 1 "${CUSTOMERS}"); do
    start_customer "${customer}" "$1"
  done
  wait
  cat "${WORK}"/customer-*.txt | grep -c "Total notes dispensed: 2" || true
}


step "1/2  ${CUSTOMERS} processes, no shared state file (each process is a separate ATM)"
paid="$(withdraw_at_the_same_time "")"
echo "    ${paid} of ${CUSTOMERS} customers paid: $((paid * 2)) notes from an ATM that holds 8"
if (( paid == CUSTOMERS )); then
  pass "shows the problem: separate processes do not share the stock"
else
  fail "expected every separate process to pay, ${paid} did"
fi

step "2/2  ${CUSTOMERS} processes sharing one state file (one ATM)"
STATE="${WORK}/atm.json"
paid="$(withdraw_at_the_same_time "${STATE}")"
refused="$(cat "${WORK}"/customer-*.txt | grep -c "Cannot dispense requested amount" || true)"
left="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["inventory"]["500"])' "${STATE}")"
echo "    ${paid} paid, ${refused} refused, ${left} notes of ₹500 left"

if (( paid == CAN_BE_PAID )); then pass "exactly ${CAN_BE_PAID} customers paid"; else fail "${paid} customers paid, expected ${CAN_BE_PAID}"; fi
if (( refused == CUSTOMERS - CAN_BE_PAID )); then pass "the others were refused"; else fail "${refused} refused, expected $((CUSTOMERS - CAN_BE_PAID))"; fi
if (( left == 0 && paid * 2 + left == 8 )); then pass "notes paid + notes left = 8 (no note paid twice)"; else fail "${left} notes left"; fi

echo
if (( failures > 0 )); then
  echo "Shared inventory test failed: ${failures} check(s)"
  exit 1
fi
echo "Shared inventory test passed"
