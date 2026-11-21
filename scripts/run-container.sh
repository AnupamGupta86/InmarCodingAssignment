#!/usr/bin/env bash
#
# Build the ATM service image, validate it, start it in a container and check that it works.
#
# Usage:
#   ./scripts/run-container.sh           test, build, validate, start and check
#   ./scripts/run-container.sh --stop    stop and remove the container
#
# The API key is taken from ATM_API_KEY if you set it; otherwise a random one is created.
# Read it back later with:  docker exec atm-dispenser printenv ATM_API_KEY

set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(./scripts/version.sh)"
IMAGE="atm-dispenser:${VERSION}"
CONTAINER="atm-dispenser"
PORT="${PORT:-8080}"
URL="http://127.0.0.1:${PORT}"


step() { echo; echo "==> $*"; }
pass() { echo "    PASS  $*"; }
fail() { echo "    FAIL  $*"; exit 1; }


run_tests() {
  step "1/7  Running the tests (the image is built only if they pass)"
  (cd atm-dispenser && python3 -m unittest discover)
}

build_image() {
  step "2/7  Building the image ${IMAGE}"
  docker build --build-arg VERSION="${VERSION}" --tag "${IMAGE}" --tag atm-dispenser:local .
}

validate_image() {
  step "3/7  Validating the image"
  ./scripts/validate-image.sh "${IMAGE}" "${VERSION}"
}

start_container() {
  step "4/7  Starting the container on ${URL}"
  stop_container

  if [[ -z "${ATM_API_KEY:-}" ]]; then
    ATM_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  fi
  export ATM_API_KEY   # passed by name below, so the key never appears on a command line

  docker run --detach \
    --name "${CONTAINER}" \
    --publish "127.0.0.1:${PORT}:8080" \
    --env ATM_API_KEY \
    --read-only \
    --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --memory 128m \
    --cpus 0.5 \
    --pids-limit 100 \
    "${IMAGE}" > /dev/null
}

wait_until_healthy() {
  step "5/7  Waiting for the health check"
  local status=""
  for _ in $(seq 1 30); do
    status="$(docker inspect --format '{{.State.Health.Status}}' "${CONTAINER}")"
    if [[ "${status}" == "healthy" ]]; then
      pass "container is healthy"
      return
    fi
    sleep 1
  done
  docker logs "${CONTAINER}"
  fail "container is '${status}' after 30 seconds"
}

check_hardening() {
  step "6/7  Checking the container is locked down"

  local user_id
  user_id="$(docker exec "${CONTAINER}" python3 -c 'import os; print(os.getuid())')"
  if [[ "${user_id}" == "10001" ]]; then
    pass "runs as non-root user ${user_id}"
  else
    fail "runs as user ${user_id}, expected 10001"
  fi

  if docker exec "${CONTAINER}" python3 -c "open('/app/probe', 'w')" 2> /dev/null; then
    fail "the filesystem is writable"
  else
    pass "the filesystem is read-only"
  fi
}

run_smoke_test() {
  step "7/7  Running the end-to-end smoke test"
  EXPECTED_VERSION="${VERSION}" python3 atm-dispenser/tests/e2e/smoke_test.py "${URL}"
}

stop_container() {
  docker rm --force "${CONTAINER}" > /dev/null 2>&1 || true
}

print_next_steps() {
  cat <<MSG

Version ${VERSION} is running. Try it:

  export ATM_API_KEY="\$(docker exec ${CONTAINER} printenv ATM_API_KEY)"
  curl -s -H "Authorization: Bearer \$ATM_API_KEY" -H 'Content-Type: application/json' \\
       -d '{"amount": 2700}' ${URL}/withdrawals

  docker logs -f ${CONTAINER}             # follow the request logs
  ./scripts/run-container.sh --stop       # stop and remove the container
MSG
}


if [[ "${1:-}" == "--stop" ]]; then
  stop_container
  echo "Container ${CONTAINER} removed"
  exit 0
fi

run_tests
build_image
validate_image
start_container
wait_until_healthy
check_hardening
run_smoke_test
print_next_steps
