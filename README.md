# ATM Cash Dispenser

[![CI](https://github.com/AnupamGupta86/InmarCodingAssignment/actions/workflows/ci.yml/badge.svg)](https://github.com/AnupamGupta86/InmarCodingAssignment/actions/workflows/ci.yml)

An ATM that pays out a withdrawal with the **fewest possible notes**, using only the notes it
actually holds. It updates its inventory only when the whole amount can be paid, and it stays
correct when several customers withdraw at the same time.

- Python 3.8 or newer, **standard library only** (no `pip install` needed)
- Works with **any denominations** (nothing is hard-coded to ₹500/₹200/₹100)
- Three ways to use it: a **Python library**, a **command-line program** and a hardened **HTTP/JSON service**
- Runs as a process or a hardened Docker container; `scripts/run-container.sh` tests, builds, starts and checks the container in one command
- 158 automated tests (unit, integration, HTTP, concurrency, fuzz, CLI) plus an end-to-end smoke test for deployed instances
- **DevSecOps pipeline** (GitHub Actions and Azure DevOps): lint, type check, SAST, secret scan, dependency audit,
  coverage gate, Dockerfile lint, image CVE scan, Dockerfile misconfiguration scan, SBOM, container checks, smoke tests and DAST

---

## Quick start

```bash
git clone https://github.com/AnupamGupta86/InmarCodingAssignment.git
cd InmarCodingAssignment

cd atm-dispenser
export PYTHONPATH=src                   # the code lives in src/ (no install needed)

python3 -m atm 2700                     # example from the brief (CLI)
python3 -m unittest discover -v         # run all tests
python3 -m atm.service                  # start the HTTP service on http://127.0.0.1:8080
```

Output of the example:

```
ATM inventory: ₹500 × 8, ₹200 × 10, ₹100 × 20
Withdrawal ₹2700: ₹500 × 5, ₹200 × 1 (Total notes dispensed: 6)
Updated inventory: ₹500 × 3, ₹200 × 9, ₹100 × 20
```

---

## Runbook

Step-by-step setup and the use cases from the brief. Commands are for macOS/Linux; on Windows use
`python` instead of `python3`.

### 1. Prerequisites

| Needed for | Tool | Check |
|---|---|---|
| Everything | Python 3.8+ | `python3 --version` |
| Container run | Docker | `docker version` |
| Optional dev checks | ruff, mypy, bandit, coverage, pip-audit | `pip install -r atm-dispenser/requirements-dev.txt` |

### 2. Set up and run the tests

```bash
git clone https://github.com/AnupamGupta86/InmarCodingAssignment.git
cd InmarCodingAssignment/atm-dispenser
export PYTHONPATH=src                  # lets Python find the code in src/
python3 -m unittest discover -v        # expect: Ran 158 tests ... OK
```

No virtual environment or package install is needed: the code uses only the standard library.
Sections 3 and 4 run from `atm-dispenser/` with `PYTHONPATH=src`; sections 5 and 6 run from the
repository root.

### 3. Use cases with the command line

| # | Use case | Command | Expected result |
|---|---|---|---|
| 1 | Example from the brief | `python3 -m atm 2700` | `₹500 × 5, ₹200 × 1 (Total notes dispensed: 6)`; updated inventory `₹500 × 3, ₹200 × 9, ₹100 × 20`; exit code 0 |
| 2 | Greedy would fail | `python3 -m atm --inventory "4:1, 3:2" 6` | `₹3 × 2` |
| 3 | Limited notes | `python3 -m atm --inventory "500:2, 200:10, 100:20" 2400` | `₹500 × 2, ₹200 × 7` |
| 4 | Cannot dispense | `python3 -m atm 250` | `Cannot dispense requested amount`; `Inventory unchanged`; exit code 1 |
| 5 | More than the ATM holds | `python3 -m atm 9000` | `Cannot dispense requested amount`; exit code 1 |
| 6 | Several withdrawals in a row | `python3 -m atm 2700 2700 800` | the inventory goes down after each one |
| 7 | Zero / negative / text / decimal | `python3 -m atm 0`, `python3 -m atm -- -5`, `python3 -m atm abc`, `python3 -m atm 12.5` | clear error message; exit code 2 |
| 8 | Invalid inventory | `python3 -m atm --inventory "{}" 100`, `--inventory "500:-1"`, `--inventory "0:5"` | `empty`, `cannot be negative`, `Invalid denomination` |
| 9 | Interactive | `python3 -m atm`, then type amounts, `q` to quit | one result per line |

Check an exit code with `echo $?` straight after the command.

### 4. Use cases with the HTTP service (local process)

Terminal 1 – start the service with an API key:

```bash
export ATM_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
python3 -m atm.service           # logs: {"message": "listening on http://127.0.0.1:8080", ...}
```

Terminal 2 – use it (copy the same `ATM_API_KEY` value into this terminal):

```bash
export ATM_API_KEY=<the same key>
AUTH="Authorization: Bearer $ATM_API_KEY"
JSON="Content-Type: application/json"
URL=http://127.0.0.1:8080

curl -s $URL/health                                                   # {"status": "ok"}
curl -s -H "$AUTH" $URL/inventory                                     # 500:8, 200:10, 100:20
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": 2700}' $URL/withdrawals  # 200: 500×5, 200×1
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": 250}'  $URL/withdrawals  # 422: Cannot dispense requested amount
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": -5}'   $URL/withdrawals  # 400: Amount cannot be negative
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": "abc"}' $URL/withdrawals # 400: Amount must be a number
curl -s -H "$JSON" -d '{"amount": 100}' $URL/withdrawals              # 401: no API key
curl -s -w ' HTTP %{http_code}\n' -H "$AUTH" $URL/inventory          # add -w to see the status code

python3 tests/e2e/smoke_test.py $URL   # all end-to-end checks: expect "All checks passed"
```

Stop the service with `Ctrl+C`. Without `ATM_API_KEY` the service runs open (it logs a warning).

### 5. Spin up the container and test it

From the repository root. One command does every step:

```bash
export ATM_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"   # optional
./scripts/run-container.sh          # or: make container
```

| Step | What it does | Pass condition |
|---|---|---|
| 1 | Runs the 158 unit, integration and security tests | `OK` (otherwise nothing is built) |
| 2 | Builds `atm-dispenser:local` (runtime code only) | build succeeds |
| 3 | Starts container `atm-dispenser` on `127.0.0.1:8080`: read-only filesystem, all capabilities dropped, `no-new-privileges`, 128 MB memory, 0.5 CPU, 100 processes | container running |
| 4 | Waits for the Docker health check (`GET /health`) | `healthy` within 30 s |
| 5 | Hardening checks inside the container | runs as user 10001 (not root); writing a file fails |
| 6 | End-to-end smoke test with the API key | `All checks passed` |

Then use it:

```bash
export ATM_API_KEY="$(docker exec atm-dispenser printenv ATM_API_KEY)"
AUTH="Authorization: Bearer $ATM_API_KEY"; JSON="Content-Type: application/json"; URL=http://127.0.0.1:8080

curl -s -H "$AUTH" $URL/inventory
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": 2700}' $URL/withdrawals   # 200: 500×5, 200×1
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": 250}'  $URL/withdrawals   # 422: Cannot dispense requested amount
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": 0}'    $URL/withdrawals   # 400: Amount must be greater than zero
docker run --rm atm-dispenser:local python -m atm 2700                   # the CLI from the same image

docker logs -f atm-dispenser        # JSON request logs (Ctrl+C to stop following)
./scripts/run-container.sh --stop   # stop and remove the container (or: make container-stop)
```

The same steps by hand, if you prefer:

```bash
(cd atm-dispenser && python3 -m unittest discover)
docker build -t atm-dispenser:local .
docker run -d --name atm-dispenser -p 127.0.0.1:8080:8080 -e ATM_API_KEY \
  --read-only --cap-drop ALL --security-opt no-new-privileges:true atm-dispenser:local
docker inspect -f '{{.State.Health.Status}}' atm-dispenser     # repeat until "healthy"
python3 atm-dispenser/tests/e2e/smoke_test.py http://127.0.0.1:8080
docker rm -f atm-dispenser
```

Docker Compose alternative: `docker compose up --build -d`, test with `make smoke`, stop with `docker compose down`.

### 6. Optional quality and security checks

```bash
pip install -r atm-dispenser/requirements-dev.txt
make lint coverage security            # ruff + mypy, coverage ≥ 95 %, bandit + pip-audit
```

The image, Dockerfile, secret and DAST scans run in CI (see [CI/CD and DevSecOps](#cicd-and-devsecops)).

### 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `Address already in use` | Another process uses port 8080: `ATM_PORT=8081 python3 -m atm.service`, or stop it (`lsof -i :8080`) |
| `cannot start: ATM_… is invalid` | Fix the named environment variable; the message says what is wrong |
| `401 Missing or invalid API key` | Send `Authorization: Bearer <key>` with the same key the service was started with |
| `415 Content-Type must be application/json` | Add `-H 'Content-Type: application/json'` |
| `₹` shows as `?` on Windows | `set PYTHONIOENCODING=utf-8` |
| Container not `healthy` | `docker logs atm-dispenser`; check port 8080 is free; `docker inspect atm-dispenser` shows the health-check output |
| `Cannot connect to the Docker daemon` | Start Docker Desktop and try again |

---

## How to run the program

Run from the `atm-dispenser` folder with `PYTHONPATH=src` set (see the runbook); use `python` on Windows.

| What | Command |
|---|---|
| One withdrawal (default inventory from the brief) | `python3 -m atm 2700` |
| Your own inventory | `python3 -m atm --inventory "500:8, 200:10, 100:20" 2700` |
| The brief's format also works | `python3 -m atm --inventory "{ 500: 8, 200: 10, 100: 20 }" 2700` |
| Several withdrawals on the same ATM, in order | `python3 -m atm 2700 2700 800` |
| Interactive mode (type amounts, `q` to quit) | `python3 -m atm` |
| A negative amount (`--` stops it being read as an option) | `python3 -m atm -- -5` |
| Help | `python3 -m atm --help` |

More examples:

```
$ python3 -m atm --inventory "4:1, 3:2" 6          # greedy would fail here
Withdrawal ₹6: ₹3 × 2 (Total notes dispensed: 2)
Updated inventory: ₹4 × 1, ₹3 × 0

$ python3 -m atm 250
Withdrawal ₹250: Cannot dispense requested amount
Inventory unchanged: ₹500 × 8, ₹200 × 10, ₹100 × 20

$ python3 -m atm abc
Invalid amount 'abc': Amount must be a number, got 'abc'

$ python3 -m atm --inventory "{}" 100
Invalid inventory: ATM inventory is empty: add at least one denomination
```

**Exit codes** (useful in scripts and pipelines; with several amounts the worst one wins):

| Code | Meaning |
|---|---|
| `0` | Every withdrawal was dispensed |
| `1` | At least one amount could not be dispensed ("Cannot dispense requested amount") |
| `2` | Invalid input (amount or inventory) |

### Using it from Python

```python
from atm import CashDispenser, CANNOT_DISPENSE

atm = CashDispenser({500: 8, 200: 10, 100: 20})

atm.dispense(2700)      # {500: 5, 200: 1}
atm.inventory           # {500: 3, 200: 9, 100: 20}
atm.dispense(250)       # None -> show CANNOT_DISPENSE; inventory unchanged
atm.dispense(-5)        # raises InvalidAmountError("Amount cannot be negative, got -5")
```

**Return structure:** `dispense(amount)` returns a `dict` of `{denomination: notes}`, largest
denomination first, exactly as in the brief. It returns `None` when the amount cannot be paid
exactly. Invalid input raises an exception with a clear message (see
[Validation](#validation-rules-and-messages)). Keeping the two apart matters: "the ATM is
short of notes" is a normal outcome, while "-5" is a mistake by the caller.

---

## Running as a service

```bash
python3 -m atm.service                                    # http://127.0.0.1:8080 (from atm-dispenser/, PYTHONPATH=src)
ATM_INVENTORY="4:1, 3:2" ATM_PORT=9000 python3 -m atm.service
```

| Method & path | Purpose | Responses |
|---|---|---|
| `GET /health` | Liveness probe | `200 {"status": "ok"}` |
| `GET /ready` | Readiness probe | `200 {"status": "ready"}` |
| `GET /inventory` | Notes in the ATM | `200 {"inventory": {"500": 8, …}, "total_cash": 8000}` |
| `POST /withdrawals` | Withdraw `{"amount": 2700}` | `200` dispensed · `400` invalid input · `422` cannot dispense · `401` no/invalid API key · `413` body > 1 KB · `415` not JSON |
| `GET /openapi.json` | OpenAPI 3 contract | `200` |

```bash
$ curl -s -X POST -H 'Content-Type: application/json' -d '{"amount": 2700}' localhost:8080/withdrawals
{"amount": 2700, "dispensed": {"500": 5, "200": 1}, "total_notes": 6, "inventory": {"500": 3, "200": 9, "100": 20}}

$ curl -s -X POST -H 'Content-Type: application/json' -d '{"amount": 250}' localhost:8080/withdrawals   # HTTP 422
{"error": "Cannot dispense requested amount", "amount": 250, "inventory": {"500": 3, "200": 9, "100": 20}}

$ curl -s -X POST -H 'Content-Type: application/json' -d '{"amount": -5}' localhost:8080/withdrawals    # HTTP 400
{"error": "Amount cannot be negative, got -5"}
```

JSON object keys are always text, so denominations appear as `"500"`.

**Configuration** (environment variables):

| Variable | Default | Meaning |
|---|---|---|
| `ATM_INVENTORY` | `500:8, 200:10, 100:20` | Notes in the ATM |
| `ATM_HOST` | `127.0.0.1` (containers: `0.0.0.0`) | Listen address |
| `ATM_PORT` | `8080` | Listen port |
| `ATM_MAX_WITHDRAWAL` | `200000` | Largest amount per withdrawal (like a real ATM limit; also caps the work per request) |
| `ATM_API_KEY` | not set | If set (16+ chars), `/inventory` and `/withdrawals` need `Authorization: Bearer <key>` |

Invalid configuration stops the service at start-up with a clear message (exit code 2).
Logs are one JSON object per line on stdout. `SIGTERM` shuts the service down cleanly.

---

## Deployment

| Where | Command | Then test with |
|---|---|---|
| Local process | `make run` | `make smoke URL=http://127.0.0.1:8080` |
| Docker | `make container` | runs the checks and smoke test itself |
| Docker Compose | `make compose-up` | `make smoke` |

The image holds only the runtime code (`atm-dispenser/src/atm`); tests run before it is built. It can also run the CLI:

```bash
docker run --rm atm-dispenser:local python -m atm 2700
```

The inventory is kept in memory, so one container is one ATM. Running several copies would give
several ATMs with separate notes; scaling out needs the notes in a shared database with row
locking (see [Production considerations](#production-considerations)).

---

## How to run the tests

```bash
cd atm-dispenser
python3 -m unittest discover -v              # all 158 tests, about 3 seconds, no installs needed
python3 -m unittest tests.test_service       # one test file
PYTHONPATH=src python3 -m doctest -v src/atm/dispenser.py   # the usage example in the docstring
python3 tests/e2e/smoke_test.py http://localhost:8080       # against a running/deployed service

pip install -r requirements-dev.txt          # optional dev tools, then from the repository root:
make lint coverage security
```

### Testing strategy

| Type | Where | What it proves |
|---|---|---|
| **Unit** | `test_validation.py`, `test_allocator.py`, `test_dispenser.py` | Every rule of the brief: validation messages, minimum notes, limited notes, greedy traps, "cannot dispense", inventory updated only on success, withdrawal limit, inventory cannot be changed from outside |
| **Correctness oracle** | `test_allocator.AgainstBruteForceTests` | 400 random ATMs: the result always equals an exhaustive search of every combination |
| **Property / fuzz** | `test_fuzz.py` | Thousands of random and hostile inputs (unicode digits, NaN, huge numbers, random bytes): validation only ever returns clean values or our own error; invariants hold over random withdrawal sequences; the HTTP service never answers 500 |
| **Concurrency** | `test_concurrency.py`, `test_service.ConcurrentHttpTests` | 20 threads competing for 8 notes; a deliberately slowed allocator to force a race; a **control test proving the race happens without the lock**; 25 parallel HTTP withdrawals |
| **Integration (HTTP)** | `test_service.py` | A real server on a free port: every endpoint, status code and error path; keep-alive; malformed HTTP |
| **Security** | `test_service.SecurityTests`, `HttpProtocolTests` | API key (missing, wrong, wrong scheme), security headers, no version disclosure, no stack-trace leaks, request-id sanitising, body size/content-type limits, path traversal, logs without request bodies |
| **Configuration** | `test_service.ConfigTests` | Every environment variable, and that bad values stop the service from starting |
| **CLI** | `test_cli.py` | Inventory parsing, output format, exit codes, interactive mode, `python -m atm` end to end |
| **Container** | `scripts/run-container.sh`, CI | Health check passes, runs as non-root user 10001, filesystem is read-only |
| **Performance** | `test_allocator.PerformanceTests` | ₹199,990 from a large ATM in well under a second |
| **End-to-end smoke** | `tests/e2e/smoke_test.py` | Against the running container (with and without an API key): health, headers, withdraw, inventory change, failure leaves inventory unchanged, every validation error, 404/405/415, 401 without key |
| **DAST** | CI (OWASP ZAP) | Active scan of the running container, driven by `/openapi.json` |

### CI/CD and DevSecOps

Every push runs `.github/workflows/ci.yml` (the same stages are in `azure-pipelines.yml` for Azure DevOps):

```
 quality ──────────┐   ruff (incl. security rules), mypy, bandit, pip-audit
 secrets            │   gitleaks over the full git history
 test (3 OS × 3 Py) │   unit, integration, concurrency, fuzz, CLI, doctest
 coverage           │   fails below 95 %
                    ▼   (the image is built only if all of the above pass)
 container            hadolint → build → Trivy image CVEs → Trivy Dockerfile scan → SBOM
                      → start hardened container → health check → non-root & read-only checks
                      → smoke test → smoke test with API key → OWASP ZAP DAST
```

Plus `codeql.yml` (CodeQL `security-extended`, weekly and on every push) and Dependabot
for actions, the base image and dev tools. Details of every control are in [Security](#security).

---

## Design

```
            ┌──────────────┐  ┌──────────────┐
  input ──▶ │   cli.py     │  │  service.py  │ ◀── HTTP/JSON (threads, API key, limits)
            └──────┬───────┘  └──────┬───────┘
                   └────────┬────────┘
                   │ amount (text or int)
            ┌──────▼───────┐   validate_amount / validate_inventory
            │ dispenser.py │ ─────────────────────────────────────▶ validation.py
            │ CashDispenser│
            │  (owns notes │   find_minimum_notes(amount, notes)
            │   + a lock)  │ ─────────────────────────────────────▶ allocator.py
            └──────────────┘                                        (pure function)
```

All modules are in `atm-dispenser/src/atm/`:

| Module | Responsibility |
|---|---|
| `atm/validation.py` | Is the input acceptable? Turns it into clean values or raises a clear error. |
| `atm/allocator.py` | Which notes pay this amount with the fewest notes? A pure calculation: no state, never changes its input. |
| `atm/dispenser.py` | The ATM. Owns the inventory, updates it only after a complete allocation, and protects it with a lock. |
| `atm/cli.py` | Command line: parsing, formatting, exit codes, interactive mode. No business rules. |
| `atm/service.py` | HTTP/JSON service: routing, request limits, API key, security headers, JSON logs, configuration. No business rules. |
| `atm/openapi.py` | The OpenAPI 3 contract served at `/openapi.json`. |
| `atm/errors.py` | `ATMError` (a `ValueError`), `InvalidAmountError`, `InvalidInventoryError`. |

Because the allocator is a pure function, it can be tested exhaustively on its own, and the
dispenser only has to worry about keeping the inventory consistent. The CLI and the service are
thin front ends over the same `CashDispenser`, so both follow exactly the same rules.

### How a withdrawal works

1. **Validate the amount** – before anything else, so bad input never touches the inventory.
2. **Lock** the ATM.
3. **Allocate** – find the fewest-note combination from the notes currently in the machine.
4. If none exists, return `None` – nothing was changed (rules 6 and 7).
5. Otherwise build the **new inventory as a complete copy** and swap it in, then return the notes
   (rule 5). The inventory is never half-updated.
6. **Unlock.**

---

## The approach

### Why "biggest note first" (greedy) is not enough

Greedy works for unlimited Indian notes, but not when notes run out or denominations are
unusual:

| Inventory | Amount | Greedy | Correct |
|---|---|---|---|
| `{4: 1, 3: 2}` | 6 | 4, then 2 is impossible → **fails** | `3 × 2` |
| `{50: 1, 20: 3}` | 60 | 50, then 10 is impossible → **fails** | `20 × 3` |
| `{4: 10, 3: 10, 1: 10}` | 6 | 4 + 1 + 1 = 3 notes | `3 × 2` = 2 notes |

### Dynamic programming ("bounded coin change")

The allocator builds a table that answers, for every amount from 0 to the target, *"what is
the fewest notes that make exactly this amount?"*. Every combination of the available notes is
considered, so the answer is always the true minimum, and "no answer" really means the amount
cannot be paid.

1. **Shrink the problem with the GCD.** Every payable amount is a multiple of the greatest
   common divisor of the denominations (100 for 500/200/100). So ₹2700 becomes 27 units, a
   table of 28 entries instead of 2,701, and ₹250 is rejected immediately.
2. **Bundle the notes.** 8 notes of ₹500 become bundles of 1, 2, 4 and 1 notes. Any count from
   0 to 8 is a sum of some of these bundles, so the algorithm only decides "use this bundle or
   not". This keeps it fast even when the ATM holds thousands of notes.
3. **Add the bundles one at a time.** For each bundle and each amount, check whether
   "this bundle + the best way to make the rest" needs fewer notes than the best found so far.
4. **Read the answer back** by walking the bundles in reverse.

**Ties:** if two combinations use the same number of notes, the one with higher-value notes is
chosen (e.g. 6 from `{4:1, 3:2, 2:1}` gives `4 + 2`, not `3 + 3`), so results are always the same.

Quick exits before the table is built: no usable notes, not enough cash in the machine, or an
amount that is not a multiple of the GCD.

### Time and space complexity

Let **S** = amount ÷ GCD of the denominations, and **B** = number of bundles
(the sum over denominations of ⌈log₂(count + 1)⌉).

| | Complexity | Notes |
|---|---|---|
| Time | **O(S × B)** | ₹2700 from the brief: S = 27, B = 13 → about 350 steps |
| Space | **O(S × B)** | one byte per (bundle, amount) to rebuild the answer, plus O(S) for the table |
| Inventory update | O(D) | D = number of denominations |

Measured: ₹199,990 from 7 denominations with up to 2,000 notes each (S ≈ 20,000, B = 75)
takes about 0.1 s.

A plain greedy algorithm is O(D) but can give wrong answers, as shown above. The textbook
bounded version (try 0..k notes of each denomination) is O(S × total notes); bundling replaces
"total notes" with its logarithm.

---

## Validation rules and messages

| Input | Result |
|---|---|
| `0` | `InvalidAmountError: Amount must be greater than zero` |
| `-500` | `InvalidAmountError: Amount cannot be negative, got -500` |
| `"abc"`, `""`, `None`, `True`, `"2_700"` | `InvalidAmountError: Amount must be a number, got …` |
| `12.5`, `"12.5"`, `2700.0` | `InvalidAmountError: Amount must be a whole number, got …` |
| `{}` | `InvalidInventoryError: ATM inventory is empty: add at least one denomination` |
| `{500: -1}` | `InvalidInventoryError: … note count … cannot be negative` |
| `{500: 2.5}`, `{500: "8"}` | `InvalidInventoryError: … note count … must be a whole number` |
| `{0: 5}`, `{-100: 5}`, `{0.5: 5}`, `{"500": 5}` | `InvalidInventoryError: Invalid denomination … must be a positive whole number` |
| Amount that cannot be made exactly (e.g. 250) | `dispense` returns `None`; CLI prints `Cannot dispense requested amount` |

Decisions worth calling out:

- Amounts may be given as `int` or whole-number text (`"2700"`, `" 2700 "`), because that is
  what a keypad or command line produces. Floats are rejected even when whole (`2700.0`): money
  should never pass through floating point.
- `True`/`False` are rejected although Python treats them as 1/0.
- A denomination with **0 notes is allowed** – it is an empty cassette, not an error.
- An inventory where every count is 0 is valid; every withdrawal then returns `None`.

---

## Requirements checklist

| Requirement from the brief | Where |
|---|---|
| Amount must be a positive whole number | `validation.validate_amount` |
| Only available notes / never more than available | `allocator` uses each note at most once; `test_allocator.NotesLimitTests` |
| Minimum number of notes | Dynamic programming; brute-force cross-check in `AgainstBruteForceTests` |
| Update inventory only after full allocation | `CashDispenser.dispense` builds the new inventory, then swaps it in |
| "Cannot dispense requested amount" | `CANNOT_DISPENSE`, printed by the CLI |
| Failed withdrawal leaves inventory unchanged | `test_dispenser.FailedWithdrawalTests` |
| Do not assume greedy | `{4:1, 3:2}` → `3 × 2`, tested |
| Any denominations | Nothing hard-coded; `test_other_denominations_are_supported` |
| Standard library only | Only `argparse`, `threading`, `math`, `functools`, `re`, `typing`, `unittest` |
| Validation messages | Table above; `test_validation.py` |
| Concurrent withdrawals must not share notes | Lock in `CashDispenser`; `test_concurrency.py`, `ConcurrentHttpTests` |

---

## Security

### Controls in this project

| Area | Control |
|---|---|
| Input | Every amount and inventory value validated; JSON only; body ≤ 1 KB; unknown fields rejected; numeric text ≤ 18 digits; per-withdrawal limit (`ATM_MAX_WITHDRAWAL`) caps the work a request can cause |
| Authentication | Optional bearer API key (`ATM_API_KEY`, ≥ 16 chars), compared in constant time; passed to the container from the environment, never stored in Git or written on a command line |
| Errors | No stack traces or internal details in responses; JSON errors for malformed HTTP too |
| HTTP headers | `nosniff`, `X-Frame-Options: DENY`, restrictive CSP, `no-referrer`, `no-store`; server version hidden |
| Availability | Socket timeout against slow clients; connection backlog sized for bursts; CPU, memory and process limits on the container |
| Logging | Structured JSON logs; request bodies never logged; client request ids sanitised against log injection |
| Container | Slim base, no extra packages, non-root UID 10001, read-only filesystem, all capabilities dropped, `no-new-privileges`, health check |
| Supply chain | Standard library only (no runtime dependencies), SBOM per build, Dependabot for actions/base image/dev tools |

### Automated security checks (CI)

| Check | Tool | Fails the build on |
|---|---|---|
| Secret scanning (full history) | gitleaks | any secret |
| SAST | bandit, ruff `S` rules, CodeQL (`security-extended`) | any finding |
| Dependency audit | pip-audit | known vulnerable version |
| Dockerfile lint | hadolint | warning or worse |
| Image CVEs | Trivy | fixable HIGH/CRITICAL |
| Dockerfile misconfiguration | Trivy config | HIGH/CRITICAL |
| Running container | health check, non-root and read-only checks, smoke tests | any failure |
| DAST against the running container | OWASP ZAP API scan (driven by `/openapi.json`) | report published as an artifact |

### Reporting a vulnerability

Please open a private security advisory on the GitHub repository rather than a public issue.

---

## Production considerations

Already in place: API key, input and size limits, per-withdrawal limit, security headers, JSON
logs, health checks, graceful shutdown and a hardened container (see
[Security](#security)). Next steps for a real bank deployment:

- **TLS** at the ingress or API gateway, ideally mutual TLS between ATM terminals and the service
- **Identity:** replace the shared API key with OAuth2/OIDC or mTLS client certificates; keep secrets in Azure Key Vault
- **Rate limiting** per client at the gateway
- **Metrics** (Prometheus: withdrawals by result, latency, notes left per denomination) and alerts
- **Hosting:** run the container on a managed platform (e.g. Azure Container Apps or AKS) behind the gateway
- **Supply chain:** pin GitHub Actions and the base image by digest, sign images (cosign) and verify signatures before deploying

The lock protects one ATM object inside one process, which is what this service is. A real
deployment where several servers update the same stock needs the guarantee from shared storage:

- **Database transaction with a row lock** – `BEGIN; SELECT … FROM cassettes WHERE atm_id = ?
  FOR UPDATE;` allocate, `UPDATE` the counts, `COMMIT`. A failed allocation simply rolls back.
- **Optimistic locking** – keep a `version` column; `UPDATE … SET counts = ?, version = version + 1
  WHERE atm_id = ? AND version = ?`, and retry if no row was updated.
- **Database constraint** `CHECK (count >= 0)` as a last line of defence.
- **Dispense failures after allocation** (the hardware jams): reserve the notes first, then confirm
  or release them once the hardware reports what was actually paid out, and keep an audit log.
- **Idempotency key** per withdrawal request, so a retried request is not paid twice.
- **Observability:** log each withdrawal (amount, notes, result) and alert when a denomination
  runs low.

---

## Assumptions

- Amounts and denominations are whole numbers of the same currency unit (rupees).
- The output lists only denominations actually dispensed; the updated inventory lists all of them,
  including those at 0.
- The CLI's default inventory is the one from the brief, so `python3 -m atm 2700` reproduces the
  expected output directly.
- The HTTP API answers `400` for invalid input and `422` when the amount is valid but cannot be paid
  with the notes available, mirroring the library's "exception vs `None`" split.
- The inventory is in memory and resets when the service restarts (persistence is out of scope).

## Project layout

```
atm-dispenser/                 the Python project
  src/atm/
    __init__.py                public API: CashDispenser, find_minimum_notes, errors
    __main__.py                enables `python -m atm`
    allocator.py               minimum-notes algorithm (pure function)
    cli.py                     command line interface
    dispenser.py               CashDispenser: inventory, lock, withdrawal limit
    errors.py                  exception classes
    openapi.py                 OpenAPI contract
    service.py                 HTTP/JSON service
    validation.py              input validation
  tests/                       158 unittest tests (unit, integration, HTTP, security, concurrency, fuzz, CLI)
    e2e/smoke_test.py          end-to-end test for a deployed service
  pyproject.toml               tool configuration (ruff, mypy, coverage, bandit)
  requirements-dev.txt         pinned dev tools (not needed at runtime)
scripts/run-container.sh       test, build, start the hardened container, check it
Dockerfile, docker-compose.yml, Makefile
.github/workflows/             ci.yml (CI/CD + DevSecOps), codeql.yml;  .github/dependabot.yml
azure-pipelines.yml            the same pipeline for Azure DevOps
```
