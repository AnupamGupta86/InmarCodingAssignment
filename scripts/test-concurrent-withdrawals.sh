#!/usr/bin/env bash
#
# Concurrent withdrawals test: several programs using the same ATM at the same moment must
# never pay out the same notes twice.
#
# Usage:  ./scripts/test-concurrent-withdrawals.sh [customers]        (default: 10 customers)
#
# The ATM holds 8 notes of ₹500. Every customer is a separate python3 process, all started at
# the same moment, and each one asks for ₹1000 (2 notes). Only 4 customers can be paid.
#
#   Scenario 1 - no shared state file: every process is its own ATM, so all customers are paid,
#                with notes that do not exist. This shows the problem.
#   Scenario 2 - one shared state file (--state-file): the file lock makes the processes take
#                turns, so exactly 4 are paid, the others are refused, and 0 notes are left.

set -euo pipefail
cd "$(dirname "$0")/.."

# Settings ---------------------------------------------------------------------------------

CUSTOMERS="${1:-10}"
NOTE_VALUE=500
NOTES_IN_ATM=8
NOTES_PER_CUSTOMER=2

AMOUNT=$(( NOTE_VALUE * NOTES_PER_CUSTOMER ))                  # ₹1000 per customer
CAN_BE_PAID=$(( NOTES_IN_ATM / NOTES_PER_CUSTOMER ))           # 4 customers
EXPECTED_PAID=$(( CUSTOMERS < CAN_BE_PAID ? CUSTOMERS : CAN_BE_PAID ))

CLI="atm-dispenser/src/cli.py"
WORK_FOLDER="$(mktemp -d)"
trap 'rm -rf "${WORK_FOLDER}"' EXIT

failed_checks=0


# Helpers ----------------------------------------------------------------------------------

heading() { echo; echo "==> $*"; }
pass()    { echo "    PASS  $*"; }
fail()    { echo "    FAIL  $*"; failed_checks=$(( failed_checks + 1 )); }

# Check a condition and report it: check "<description>" <condition...>
check() {
  local description="$1"; shift
  if "$@"; then pass "${description}"; else fail "${description}"; fi
}

# Start one customer in the background; its output goes to its own file.
#   $1 = customer number, $2 = state file to share ("" for none)
start_customer() {
  local output="${WORK_FOLDER}/customer-$1.txt"
  if [[ -n "$2" ]]; then
    python3 "${CLI}" --inventory "${NOTE_VALUE}:${NOTES_IN_ATM}" --state-file "$2" "${AMOUNT}" > "${output}" 2>&1 &
  else
    python3 "${CLI}" --inventory "${NOTE_VALUE}:${NOTES_IN_ATM}" "${AMOUNT}" > "${output}" 2>&1 &
  fi
}

# Start every customer at once and wait until all of them have finished.
#   $1 = state file to share ("" for none)
run_all_customers_at_once() {
  rm -f "${WORK_FOLDER}"/customer-*.txt
  for customer in $(seq 1 "${CUSTOMERS}"); do
    start_customer "${customer}" "$1"
  done
  wait
}

# How many customers' output contains the given text.
customers_who_saw() {
  cat "${WORK_FOLDER}"/customer-*.txt | grep -c "$1" || true
}

# Notes left in a state file.
notes_left_in() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["inventory"][sys.argv[2]])' "$1" "${NOTE_VALUE}"
}


# Scenarios --------------------------------------------------------------------------------

scenario_1_without_a_shared_state_file() {
  heading "Scenario 1: ${CUSTOMERS} customers, no shared state file (each process is its own ATM)"
  run_all_customers_at_once ""

  local paid
  paid="$(customers_who_saw "Total notes dispensed: ${NOTES_PER_CUSTOMER}")"
  echo "    ${paid} customers paid: $(( paid * NOTES_PER_CUSTOMER )) notes from an ATM that holds ${NOTES_IN_ATM}"

  check "shows the problem: separate processes do not share the notes" test "${paid}" -eq "${CUSTOMERS}"
}

scenario_2_with_one_shared_state_file() {
  heading "Scenario 2: ${CUSTOMERS} customers sharing one state file (one ATM)"
  local state_file="${WORK_FOLDER}/atm.json"
  run_all_customers_at_once "${state_file}"

  local paid refused left
  paid="$(customers_who_saw "Total notes dispensed: ${NOTES_PER_CUSTOMER}")"
  refused="$(customers_who_saw "Cannot dispense requested amount")"
  left="$(notes_left_in "${state_file}")"
  echo "    ${paid} paid, ${refused} refused, ${left} notes of ₹${NOTE_VALUE} left"

  check "exactly ${EXPECTED_PAID} customers paid" test "${paid}" -eq "${EXPECTED_PAID}"
  check "the other $(( CUSTOMERS - EXPECTED_PAID )) were refused" test "${refused}" -eq $(( CUSTOMERS - EXPECTED_PAID ))
  check "notes paid + notes left = ${NOTES_IN_ATM} (no note paid twice)" \
    test $(( paid * NOTES_PER_CUSTOMER + left )) -eq "${NOTES_IN_ATM}"
}

print_result() {
  echo
  if (( failed_checks > 0 )); then
    echo "Concurrent withdrawals test FAILED: ${failed_checks} check(s)"
    exit 1
  fi
  echo "Concurrent withdrawals test passed"
}


# Main -------------------------------------------------------------------------------------

scenario_1_without_a_shared_state_file
scenario_2_with_one_shared_state_file
print_result
