#!/usr/bin/env bash
#
# Check a built image before it is run or shipped.
#
# Usage:  ./scripts/validate-image.sh <image> <expected version>
#   e.g.  ./scripts/validate-image.sh atm-dispenser:1.0.20261120183012 1.0.20261120183012

set -euo pipefail

IMAGE="${1:?usage: validate-image.sh <image> <expected version>}"
VERSION="${2:?usage: validate-image.sh <image> <expected version>}"
MAX_SIZE_MB=250
failures=0

pass() { echo "    PASS  $*"; }
fail() { echo "    FAIL  $*"; failures=$((failures + 1)); }

inspect() { docker image inspect --format "$1" "${IMAGE}"; }

echo "Validating image ${IMAGE}"

# 1. Metadata
label_version="$(inspect '{{index .Config.Labels "org.opencontainers.image.version"}}')"
if [[ "${label_version}" == "${VERSION}" ]]; then pass "version label is ${VERSION}"; else fail "version label is '${label_version}', expected ${VERSION}"; fi

if inspect '{{range .Config.Env}}{{println .}}{{end}}' | grep -qx "ATM_VERSION=${VERSION}"; then
  pass "ATM_VERSION is ${VERSION}"
else
  fail "ATM_VERSION is not ${VERSION}"
fi

# 2. Security settings baked into the image
user="$(inspect '{{.Config.User}}')"
if [[ "${user}" == "10001" ]]; then pass "runs as non-root user 10001"; else fail "user is '${user}', expected 10001"; fi

if [[ -n "$(inspect '{{if .Config.Healthcheck}}yes{{end}}')" ]]; then pass "has a health check"; else fail "no health check"; fi

if inspect '{{range $port, $_ := .Config.ExposedPorts}}{{println $port}}{{end}}' | grep -qx "8080/tcp"; then
  pass "exposes port 8080"
else
  fail "does not expose port 8080"
fi

# 3. Contents: only the application modules, no tests, folders or build files
contents="$(docker run --rm --entrypoint python3 "${IMAGE}" -c "import os; print(' '.join(sorted(os.listdir('/app'))))")"
unexpected="$(tr ' ' '\n' <<< "${contents}" | grep -vE '^[a-z_]+\.py$' | grep -v '^$' || true)"
if [[ -z "${unexpected}" && "${contents}" != *test* && "${contents}" == *service.py* ]]; then
  pass "/app contains only the application modules"
else
  fail "/app contains: ${contents}"
fi

# 4. Size
size_mb=$(( $(inspect '{{.Size}}') / 1024 / 1024 ))
if (( size_mb <= MAX_SIZE_MB )); then pass "size ${size_mb} MB (limit ${MAX_SIZE_MB} MB)"; else fail "size ${size_mb} MB is over ${MAX_SIZE_MB} MB"; fi

# 5. No packaging tools (pip, setuptools) that the app does not need
if docker run --rm --entrypoint python3 "${IMAGE}" -c \
    "import importlib.util, sys; sys.exit(any(importlib.util.find_spec(m) for m in ('pip', 'setuptools', 'ensurepip')))"; then
  pass "no pip, setuptools or ensurepip in the image"
else
  fail "pip, setuptools or ensurepip is still in the image"
fi

# 6. The folder for the optional state file is writable by the app user
if docker run --rm --entrypoint python3 "${IMAGE}" -c "open('/data/probe', 'w').close()"; then
  pass "/data is writable by user 10001 (for ATM_STATE_FILE)"
else
  fail "/data is not writable by user 10001"
fi

# 7. The code in the image works
if docker run --rm "${IMAGE}" python3 cli.py 2700 | grep -q "Total notes dispensed: 6"; then
  pass "CLI in the image dispenses the example from the brief"
else
  fail "CLI in the image did not dispense the example"
fi

if (( failures > 0 )); then
  echo "Image validation failed: ${failures} check(s)"
  exit 1
fi
echo "Image validation passed"
