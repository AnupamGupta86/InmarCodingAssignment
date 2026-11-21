"""End-to-end smoke test for a DEPLOYED ATM service (standard library only).

    python3 tests/e2e/smoke_test.py http://localhost:8080
    ATM_API_KEY=... python3 tests/e2e/smoke_test.py http://localhost:8080
    EXPECTED_VERSION=1.0.20261120183012 python3 tests/e2e/smoke_test.py http://localhost:8080

Run it after every start (local process or container). It talks to the service
over real HTTP, checks the functional behaviour, the error handling and the security headers,
and exits with 0 when everything passes, 1 otherwise.

It does not assume a fresh ATM: it compares the inventory before and after each step, so it
can run against a service that has already been used.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

API_KEY = os.environ.get("ATM_API_KEY", "")
failures = []


def call(base_url, method, path, body=None, authorised=True, content_type="application/json"):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base_url + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", content_type)
    if authorised and API_KEY:
        request.add_header("Authorization", f"Bearer {API_KEY}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, _json(response.read()), dict(response.headers)
    except urllib.error.HTTPError as error:
        with error:  # an error response is still an open connection: close it
            return error.code, _json(error.read()), dict(error.headers)


def _json(raw):
    try:
        return json.loads(raw or b"null")
    except ValueError:
        return {"error": f"response is not JSON: {raw[:80]!r}"}


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def wait_until_ready(base_url, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if call(base_url, "GET", "/health")[0] == 200:
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def main(base_url, api_key=None):
    global API_KEY
    API_KEY = os.environ.get("ATM_API_KEY", "") if api_key is None else api_key
    failures.clear()
    base_url = base_url.rstrip("/")
    print(f"Smoke testing {base_url}")

    if not wait_until_ready(base_url):
        print("  FAIL  service did not become ready")
        return 1

    status, body, headers = call(base_url, "GET", "/health")
    check("GET /health returns 200 ok", status == 200 and body.get("status") == "ok", f"{status} {body}")
    expected_version = os.environ.get("EXPECTED_VERSION")
    if expected_version:
        check(f"service reports version {expected_version}", body.get("version") == expected_version,
              f"got {body.get('version')}")
    check("security headers present", headers.get("X-Content-Type-Options") == "nosniff"
          and "default-src 'none'" in headers.get("Content-Security-Policy", ""))
    check("server version not exposed", headers.get("Server") == "atm-service", headers.get("Server"))

    status, body, _ = call(base_url, "GET", "/inventory")
    check("GET /inventory returns 200", status == 200, f"{status} {body}")
    if status != 200:
        hint = " - set ATM_API_KEY to the service's key" if status == 401 else ""
        print(f"\nCannot continue without the inventory{hint}")
        return 1
    before = {int(k): v for k, v in body["inventory"].items()}

    # Choose an amount the current inventory can pay: the smallest note that is still available.
    available = sorted(d for d, c in before.items() if c > 0)
    if available:
        amount = available[0]
        status, body, _ = call(base_url, "POST", "/withdrawals", {"amount": amount})
        check(f"withdraw {amount} succeeds", status == 200, f"{status} {body}")
        if status == 200:
            after = {int(k): v for k, v in body["inventory"].items()}
            paid = sum(int(d) * c for d, c in body["dispensed"].items())
            check("dispensed notes add up to the amount", paid == amount, f"{paid} != {amount}")
            check("inventory reduced by exactly the dispensed notes",
                  all(after[d] == before[d] - body["dispensed"].get(str(d), 0) for d in before))
            before = after

    impossible = sum(d * c for d, c in before.items()) + 1  # more than all cash in the machine
    status, body, _ = call(base_url, "POST", "/withdrawals", {"amount": impossible})
    check("impossible amount is rejected (422)", status in (400, 422), f"{status} {body}")
    status, body, _ = call(base_url, "GET", "/inventory")
    check("failed withdrawal leaves inventory unchanged",
          {int(k): v for k, v in body["inventory"].items()} == before)

    for amount, expected in ((0, "greater than zero"), (-5, "cannot be negative"),
                             ("abc", "must be a number"), (12.5, "whole number")):
        status, body, _ = call(base_url, "POST", "/withdrawals", {"amount": amount})
        check(f"invalid amount {amount!r} returns 400", status == 400 and expected in body.get("error", ""),
              f"{status} {body}")

    check("unknown path returns 404", call(base_url, "GET", "/does-not-exist")[0] == 404)
    check("wrong method returns 405", call(base_url, "GET", "/withdrawals")[0] == 405)
    check("non-JSON content type returns 415",
          call(base_url, "POST", "/withdrawals", {"amount": 100}, content_type="text/plain")[0] == 415)
    if API_KEY:
        check("request without API key returns 401",
              call(base_url, "POST", "/withdrawals", {"amount": 100}, authorised=False)[0] == 401)

    print(f"\n{'All checks passed' if not failures else f'{len(failures)} check(s) failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"))
