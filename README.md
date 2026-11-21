# ATM Cash Dispenser

[![CI](https://github.com/AnupamGupta86/InmarCodingAssignment/actions/workflows/ci.yml/badge.svg)](https://github.com/AnupamGupta86/InmarCodingAssignment/actions/workflows/ci.yml)

1. [Overview & Assumptions](#1-overview--assumptions)
2. [Solution Architecture & Workflow](#2-solution-architecture--workflow)
3. [Project Structure](#3-project-structure)
4. [Requirements Coverage](#4-requirements-coverage)
5. [Installation, Configuration & Usage](#5-installation-configuration--usage)
6. [Validation & Testing Strategy](#6-validation--testing-strategy)
7. [Security & DevSecOps](#7-security--devsecops)
8. [Troubleshooting & FAQ](#8-troubleshooting--faq)

## 1. Overview & Assumptions

### 1.1 Introduction

**ATM Cash Dispenser** decides which notes an ATM should pay out for a withdrawal. It always uses
the **fewest possible notes**, only uses notes the machine actually holds, and updates its
inventory only when the whole amount can be paid. Several customers can withdraw at the same time
without ever being given the same notes.

```
ATM inventory: ₹500 × 8, ₹200 × 10, ₹100 × 20
Withdrawal ₹2700: ₹500 × 5, ₹200 × 1 (Total notes dispensed: 6)
Updated inventory: ₹500 × 3, ₹200 × 9, ₹100 × 20
```

Key features:

- **Correct, not just greedy:** finds the true minimum even where "biggest note first" fails
  (`{4: 1, 3: 2}` and ₹6 → `₹3 × 2`).
- **Any denominations:** nothing is hard-coded to ₹500/₹200/₹100.
- **Safe failures:** an amount that cannot be paid exactly gives *"Cannot dispense requested
  amount"* and leaves the inventory untouched; invalid input gets a clear message.
- **Thread-safe:** a lock makes "allocate notes → update inventory" one indivisible step.
- **Two ways to use it:** a **command-line program** and a **hardened HTTP/JSON service** (in Docker).
- **Small and object-oriented:** three source files, standard library only, Python 3.14.8.
- **Tested and shipped like production code:** 211 automated tests, a versioned and validated
  Docker image, and a DevSecOps pipeline for GitHub Actions and Azure DevOps.

### 1.2 Assumptions

- Amounts and denominations are whole numbers of the same currency unit (rupees).
- The output lists only denominations actually dispensed; the updated inventory lists all of them,
  including those at 0.
- The default inventory is the one from the brief, so `python3 atm-dispenser/src/cli.py 2700`
  reproduces the expected output directly.
- A single withdrawal is limited to 200,000 by default, as real ATMs limit withdrawals. Change it with
  `--max-withdrawal` (CLI) or `ATM_MAX_WITHDRAWAL` (service).
- An amount that cannot be paid with the notes available is a normal outcome, not an error: the
  CLI exits with code 1 and the service answers `422`. Invalid input is an error: exit code 2 / `400`.
- The inventory is kept in memory by default and resets on restart. With a state file
  (`--state-file` / `ATM_STATE_FILE`) it is saved as JSON after every withdrawal, survives restarts
  and can be shared by several processes. An existing state file wins over the starting inventory.
- Python **3.14.8** must be installed (`python3 --version`) to run the command line and the tests.
  The service runs in Docker, which brings its own Python, so it needs only Docker.
- "Minimum notes" means the fewest notes in total, not the fewest denominations. When two
  combinations need the same number of notes, the one with higher-value notes is chosen, so the
  result is always the same for the same input.
- One running program or service is one ATM. The in-process lock protects concurrent withdrawals
  inside it; several processes on one machine share one stock safely through the same state file
  (a file lock, proved by `scripts/shared-inventory-test.sh`). Several machines would need a
  shared database instead.

---

## 2. Solution Architecture & Workflow

### 2.1 High-Level Design (HLD)

**Components and where they run**

The solution can be used in **two ways**, both built on the same core (`dispenser.py`):

| | Approach 1: command-line program | Approach 2: HTTP/JSON service |
|---|---|---|
| Runs | on your machine, with `python3` 3.14.8 | in a hardened Docker container |
| Started with | `python3 atm-dispenser/src/cli.py 2700` | `./scripts/run-container.sh` |
| Used by | an operator or tester at a terminal | an ATM terminal or any HTTP client |
| Inventory | per run, or a shared JSON state file (`--state-file`) | per container, or a JSON state file on a volume (`ATM_STATE_FILE`) |

```mermaid
flowchart LR
    operator["Operator / tester<br/>(terminal)"]
    client["ATM terminal or<br/>client application"]
    zap["OWASP ZAP<br/>(CI only)"]

    subgraph local["Approach 1: command line — local machine, python3 3.14.8"]
        cli["cli.py<br/>CommandLine"]
        core1["dispenser.py<br/>Validator · NoteAllocator · CashDispenser"]
        cli --> core1
    end

    subgraph container["Approach 2: HTTP service — Docker container atm-dispenser:version<br/>(non-root, read-only, resource limits)"]
        svc["service.py<br/>ATMService · ATMServer · ATMRequestHandler"]
        cli2["cli.py<br/>(docker run … python3 cli.py)"]
        core2["dispenser.py<br/>Validator · NoteAllocator · CashDispenser"]
        svc --> core2
        cli2 --> core2
    end

    operator -->|"python3 atm-dispenser/src/cli.py 2700"| cli
    client -->|"HTTP :8080 — /health, /inventory, POST /withdrawals"| svc
    zap -->|"GET /openapi.json, then active scan"| svc

    state[("Optional JSON state file<br/>atm.json + file lock")]
    core1 -.->|"--state-file"| state
    core2 -.->|"ATM_STATE_FILE on a volume"| state
```

Both front ends are thin: every business rule lives once, in `dispenser.py`, so the command line
and the service always behave the same way.

**Build and delivery**

```mermaid
flowchart LR
    dev["Developer"] -->|git push| repo["GitHub repository"]
    repo --> gates["Quality gates<br/>lint · types · CodeQL · secrets ·<br/>tests on Linux and macOS · coverage ≥ 70%"]
    gates -->|all pass| build["Build image<br/>atm-dispenser:major.minor.datetime"]
    build --> validate["Validate and scan<br/>validate-image.sh · Trivy · SBOM"]
    validate --> run["Run hardened container<br/>health · non-root · read-only"]
    run --> e2e["End-to-end smoke tests<br/>+ OWASP ZAP DAST"]
    e2e --> reports["Artifacts<br/>SBOM · ZAP report · coverage"]
```

The same pipeline is defined for Azure DevOps in `azure-pipelines.yml`.

### 2.2 Low-Level Design (LLD)

**Classes** (three source files in `atm-dispenser/src/`)

```mermaid
classDiagram
    direction LR

    class ValueError
    class ATMError
    class InvalidAmountError
    class InvalidInventoryError
    class ConfigError
    ValueError <|-- ATMError
    ATMError <|-- InvalidAmountError
    ATMError <|-- InvalidInventoryError
    ValueError <|-- ConfigError

    class Validator {
        +WHOLE_NUMBER_TEXT
        +MAX_DIGITS = 18
        +amount(value)$ int
        +inventory(mapping)$ Notes
        +parse_inventory(text)$ Notes
    }

    class NoteAllocator {
        +find_minimum_notes(amount, inventory) Notes or None
        +split_into_bundles(count)$ list
        -_usable_notes(amount, inventory)$
        -_fill_table(target, unit, bundles)
        -_read_back(target, unit, bundles, used)$
    }

    class CashDispenser {
        -_notes : Notes
        -_lock : threading.Lock
        -_allocator : NoteAllocator
        +max_withdrawal : int or None
        -_store : InventoryStore or None
        +CashDispenser(inventory, max_withdrawal, allocator, store)
        +dispense(amount) Notes or None
        +dispense_with_inventory(amount) tuple
        +inventory : Notes
        +total_cash : int
    }

    class InventoryStore {
        +path : Path
        +locked() context
        +exists() bool
        +load() Notes
        +save(notes)
    }

    class CommandLine {
        +run(argv) int
        +withdraw(atm, amount_text) int
        +run_interactive(atm, lines) int
        +show_inventory(atm)
        +format_notes(notes)$ str
        +format_withdrawal(amount, notes)$ str
    }

    class ServiceConfig {
        +atm : CashDispenser
        +host, port, api_key, version
        +from_environment(environ)$ ServiceConfig
    }

    class ATMService {
        +run() int
    }

    class ThreadingHTTPServer
    class ATMServer {
        +atm : CashDispenser
        +api_key, version
        +request_queue_size = 128
    }
    ThreadingHTTPServer <|-- ATMServer

    class BaseHTTPRequestHandler
    class ATMRequestHandler {
        +ROUTES
        +PROTECTED
        -_get_health()
        -_get_openapi()
        -_get_inventory()
        -_post_withdrawal()
        -_authorised() bool
        -_read_json_body()
        -_send(status, body)
    }
    BaseHTTPRequestHandler <|-- ATMRequestHandler

    class JsonLogFormatter {
        +format(record) str
    }

    CashDispenser --> Validator : validates input
    CashDispenser o-- NoteAllocator : allocates notes
    CashDispenser o-- InventoryStore : optional state file
    InventoryStore --> Validator : validates the file
    CommandLine --> CashDispenser : uses
    CommandLine --> Validator
    ATMService --> ServiceConfig : reads environment
    ATMService --> JsonLogFormatter : configures logging
    ServiceConfig --> CashDispenser : creates
    ATMService --> ATMServer : starts
    ATMServer --> CashDispenser : owns one ATM
    ATMServer ..> ATMRequestHandler : one per connection
    ATMRequestHandler --> Validator
```

| File | Classes | Responsibility |
|---|---|---|
| `dispenser.py` | `ATMError`, `InvalidAmountError`, `InvalidInventoryError`, `Validator`, `NoteAllocator`, `CashDispenser`, `InventoryStore` | The business rules: validation, the minimum-notes algorithm, the inventory and its lock, the optional JSON state file |
| `cli.py` | `CommandLine` | Arguments, output format, exit codes, interactive mode (withdraw, `i` inventory, `q` quit). No business rules. |
| `service.py` | `ConfigError`, `ServiceConfig`, `ATMService`, `ATMServer`, `ATMRequestHandler`, `JsonLogFormatter`, `OPENAPI_SPEC` | HTTP routing, request limits, API key, security headers, JSON logs, configuration, the OpenAPI contract. No business rules. |

**A withdrawal through the service**

```mermaid
sequenceDiagram
    participant C as Client
    participant H as ATMRequestHandler
    participant V as Validator
    participant D as CashDispenser
    participant A as NoteAllocator

    C->>H: POST /withdrawals {"amount": 2700}
    H->>H: route, API key, Content-Type, body ≤ 1 KB, JSON, only "amount"
    H->>V: amount(2700)
    H->>D: dispense_with_inventory(2700)
    D->>V: amount(2700) (the core never trusts its caller)
    D->>D: withdrawal limit
    alt invalid amount or above the limit
        D-->>H: InvalidAmountError
        H-->>C: 400 {"error": "..."}
    else valid amount
        D->>D: acquire lock
        D->>A: find_minimum_notes(2700, notes)
        alt the notes can pay the amount
            A-->>D: {500: 5, 200: 1}
            D->>D: build the new inventory, swap it in, release lock
            D-->>H: notes, inventory after
            H-->>C: 200 {"dispensed": {"500": 5, "200": 1}, ...}
        else no combination pays it exactly
            A-->>D: None
            D->>D: release lock (inventory unchanged)
            D-->>H: None, inventory
            H-->>C: 422 {"error": "Cannot dispense requested amount"}
        end
    end
    H->>H: JSON log line with status, duration and request id (never the body)
```

**Service start-up and shutdown**

```mermaid
sequenceDiagram
    participant Docker
    participant S as ATMService
    participant Cfg as ServiceConfig
    participant Srv as ATMServer

    Docker->>S: python3 service.py
    S->>S: JSON logging (JsonLogFormatter)
    S->>Cfg: from_environment(ATM_* variables)
    alt invalid configuration or port in use
        Cfg-->>S: ConfigError / OSError
        S-->>Docker: exit code 2, clear log message
    else valid
        Cfg-->>S: CashDispenser, host, port, api_key, version
        S->>Srv: ATMServer(...).serve_forever()
        Docker->>S: SIGTERM (docker stop)
        S->>Srv: shutdown(), server_close()
        S-->>Docker: exit code 0
    end
```

Design decisions:

- **Separation of responsibilities.** `NoteAllocator` is a pure calculation, so it can be tested
  exhaustively on its own; `CashDispenser` only keeps the inventory consistent; the front ends
  only translate input and output.
- **Concurrency.** The lock covers reading the inventory, allocating and updating it. The new
  inventory is built as a complete copy and swapped in, so it is never half-updated.
- **Replaceable allocator.** `CashDispenser(..., allocator=...)` accepts another `NoteAllocator`;
  the concurrency tests use this to slow allocation down and prove the lock works.
- **One process = one ATM, unless they share a state file.** Separate processes have separate
  notes; with one state file they take turns through its file lock and behave as one ATM
  (`scripts/shared-inventory-test.sh`).

Alternatives considered:

| Option | Why it was not chosen |
|---|---|
| Greedy (biggest note first) | Fast, but wrong for some inventories (section [2.3.1](#231-why-biggest-note-first-greedy-is-not-enough)) |
| Try every combination (brute force) | Always correct, but exponential; used only in the tests as an oracle |
| Integer linear programming solver | Correct and fast, but needs a third-party library (the brief allows the standard library only) |
| Web framework (Flask, FastAPI) | Adds dependencies to patch and scan; `http.server` with explicit limits is enough for three endpoints |
| A queue or a single worker thread instead of a lock | Also safe, but more moving parts for the same guarantee |
| SQLite or another database for the state | Also standard library (SQLite), but a JSON file is readable by eye and the file lock is enough on one machine; a database is the step for several machines |

### 2.3 Algorithm: Approach And Complexity

#### 2.3.1 Why "Biggest Note First" (Greedy) Is Not Enough

| Inventory | Amount | Greedy | Correct |
|---|---|---|---|
| `{4: 1, 3: 2}` | 6 | 4, then 2 is impossible → **fails** | `3 × 2` |
| `{50: 1, 20: 3}` | 60 | 50, then 10 is impossible → **fails** | `20 × 3` |
| `{4: 10, 3: 10, 1: 10}` | 6 | 4 + 1 + 1 = 3 notes | `3 × 2` = 2 notes |

#### 2.3.2 Dynamic Programming ("Bounded Coin Change")

`NoteAllocator` builds a table that answers, for every amount from 0 to the target, *"what is the
fewest notes that make exactly this amount?"*. Every combination of the available notes is
considered, so the answer is always the true minimum, and "no answer" really means the amount
cannot be paid.

1. **Shrink the problem with the GCD (Greatest Common Divisor).** Every payable amount is a
   multiple of the greatest common divisor of the denominations (100 for 500/200/100). So ₹2700 becomes 27 units, and
   ₹250 is rejected immediately.
2. **Only count useful notes.** No denomination can use more than `amount ÷ value` notes, so a
   machine reporting a trillion notes is no slower than one with just enough.
3. **Bundle the notes.** 8 notes of ₹500 become bundles of 1, 2, 4 and 1 notes. Any count from
   0 to 8 is a sum of some of these bundles, so the algorithm only decides "use this bundle or not".
4. **Add the bundles one at a time.** For each bundle and each amount, check whether
   "this bundle + the best way to make the rest" needs fewer notes than the best found so far.
5. **Read the answer back** by walking the bundles in reverse.

**Ties:** if two combinations use the same number of notes, the one with higher-value notes is
chosen (6 from `{4:1, 3:2, 2:1}` gives `4 + 2`), so results are always the same.

#### 2.3.3 Time And Space Complexity

Let **S** = amount ÷ GCD (greatest common divisor) of the denominations, and **B** = number of bundles
(the sum over denominations of ⌈log₂(usable notes + 1)⌉).

| | Complexity | Notes |
|---|---|---|
| Time | **O(S × B)** | ₹2700 from the brief: S = 27, B = 12 → about 336 steps |
| Space | **O(S × B)** | one byte per (bundle, amount) to rebuild the answer, plus O(S) for the table |
| Inventory update | O(D) | D = number of denominations |

Measured: ₹199,990 from 7 denominations with up to 2,000 notes each takes about 0.1 s. Greedy is
O(D) but can be wrong; the textbook bounded version is O(S × total notes); bundling replaces
"total notes" with its logarithm.

### 2.4 Workflow: A Withdrawal

```mermaid
flowchart TD
    start(["Withdrawal request<br/>CLI amount or POST /withdrawals"]) --> validate{"Amount valid?<br/>whole number, > 0, within the limit"}
    validate -->|no| invalid["Invalid input<br/>CLI exit code 2 · HTTP 400"]
    validate -->|yes| lock["Lock the ATM<br/>(+ file lock if a state file is used)"]
    lock --> reload["Re-read the notes from the state file<br/>(only with a state file)"]
    reload --> allocate["Allocate: fewest notes<br/>from the notes available"]
    allocate --> found{"Exact combination<br/>found?"}
    found -->|no| refuse["Unlock · inventory unchanged<br/>'Cannot dispense requested amount'<br/>CLI exit code 1 · HTTP 422"]
    found -->|yes| update["Build the new inventory as a complete copy<br/>save it to the state file (temporary file + rename)<br/>swap it in"]
    update --> paid["Unlock · return the notes<br/>CLI exit code 0 · HTTP 200"]
```

Invalid input never touches the inventory, a refused withdrawal changes nothing (requirements R5
and R6 in [section 4](#4-requirements-coverage)), and the inventory is updated only after the
whole amount is allocated (R4). If saving the state file fails, nothing changes.

### 2.5 Workflow: Build, Validate And Run

The same order is used on a laptop (`scripts/run-container.sh`) and in CI. Nothing is built until
the tests pass, and nothing is run until the image is validated.

```mermaid
flowchart LR
    tests["1. Tests<br/>211 automated tests"] -->|pass| version["2. Version<br/>major.minor.datetime (IST)"]
    version --> build["3. Build image<br/>atm-dispenser:version"]
    build --> validate["4. Validate image<br/>validate-image.sh"]
    validate -->|pass| run["5. Start hardened container<br/>read-only, non-root, limits"]
    run --> health["6. Health check<br/>GET /health"]
    health --> harden["7. Non-root and<br/>read-only checks"]
    harden --> smoke["8. End-to-end<br/>smoke test"]
    tests -.->|fail| stop(["Stop: nothing is built or run"])
    validate -.->|fail| stop
```

**Build version:** `major.minor.datetime`, for example `1.0.20261120183012`. Major and minor come
from `atm-dispenser/pyproject.toml`; the rest is the build date and time in **IST** (India Standard
Time, UTC+05:30) (`scripts/version.sh`). The version is the image tag and label, is set as
`ATM_VERSION` in the image, and is reported by `GET /health`, so the smoke test can confirm the
running container is exactly the image that was built.

---

## 3. Project Structure

```
InmarCodingAssignment/
├── atm-dispenser/                   the Python project
│   ├── src/
│   │   ├── dispenser.py             ATM core: Validator, NoteAllocator, CashDispenser, InventoryStore, errors
│   │   ├── cli.py                   command line: CommandLine
│   │   └── service.py               HTTP/JSON service: ATMService, ATMServer, ATMRequestHandler
│   ├── tests/                       211 unittest tests, one file per source file
│   │   ├── test_dispenser.py        Validator, NoteAllocator, CashDispenser, state file, concurrency, property tests
│   │   ├── test_cli.py              CommandLine
│   │   ├── test_service.py          HTTP service, security, HTTP fuzzing, the smoke-test script
│   │   └── e2e/smoke_test.py        end-to-end test for a running service
│   ├── pyproject.toml               version, Python version and tool configuration
│   └── requirements-dev.txt         pinned dev tools (not needed at runtime)
├── scripts/
│   ├── run-container.sh             test, build, validate, start and check the container
│   ├── shared-inventory-test.sh     several processes share one state file and never pay the same notes
│   ├── validate-image.sh            checks a built image before it is run
│   └── version.sh                   prints the build version (major.minor.datetime)
├── .github/workflows/ci.yml         CI/CD with DevSecOps gates (on every push and weekly)
├── azure-pipelines.yml              the same pipeline for Azure DevOps
├── Dockerfile                       runtime image (application code only)
└── Makefile                         shortcuts for the commands below
```

---

## 4. Requirements Coverage

| # | Requirement from the brief | Where |
|---|---|---|
| R1 | Amount must be a positive whole number | `Validator.amount` |
| R2 | Only available notes / never more than available | `NoteAllocator` uses each note at most once; `test_dispenser.NotesLimitTests` |
| R3 | Minimum number of notes | Dynamic programming; brute-force cross-check in `AgainstBruteForceTests` |
| R4 | Update inventory only after full allocation | `CashDispenser` builds the new inventory, then swaps it in |
| R5 | "Cannot dispense requested amount" | `CANNOT_DISPENSE`, printed by the CLI, returned by the service |
| R6 | Failed withdrawal leaves inventory unchanged | `test_dispenser.FailedWithdrawalTests` |
| R7 | Do not assume greedy | `{4:1, 3:2}` → `3 × 2`, tested |
| R8 | Any denominations | Nothing hard-coded; `test_other_denominations_are_supported` |
| R9 | Standard library only | No third-party packages at runtime (`dependencies = []`): `argparse`, `threading`, `fcntl`, `http.server`, `json`, `math`, `tempfile`, `unittest` and other built-in modules |
| R10 | Validation messages | [Section 6.1](#61-validation-rules-and-messages); `test_dispenser.InvalidAmountTests`, `InvalidInventoryTests` |
| R11 | Concurrent withdrawals must not share notes | Lock in `CashDispenser` (threads) and the state-file lock (processes); `test_dispenser.ConcurrentWithdrawalTests`, `StateFileTests`, `test_service.ConcurrentHttpTests`, `scripts/shared-inventory-test.sh` |
| R12 | Positive and negative test cases | 211 tests ([section 6.2](#62-testing-strategy)); every validation error and every "cannot dispense" path |
| R13 | README: design, how to run and test, complexity | Sections [2](#2-solution-architecture--workflow) (design and [complexity](#233-time-and-space-complexity)), [5](#5-installation-configuration--usage) (how to run) and [6.2](#62-testing-strategy) (tests) |
| R14 | Readable, maintainable code | Three small object-oriented modules with type hints; ruff and mypy checked in CI |

---

## 5. Installation, Configuration & Usage

All commands run from the repository root and use `python3`.

> **macOS (zsh):** run `setopt interactivecomments` once (add it to `~/.zshrc` to keep it), so the
> `# comments` after the commands below are ignored when you paste them.

| Platform | How to run |
|---|---|
| macOS, Linux | `python3` 3.14.8 for the CLI and tests; Docker for the service |
| Windows | Docker (sections [5.4](#54-build-validate-and-run-the-service-in-a-container) and [5.7](#57-http-service-reference)), or the macOS/Linux commands inside WSL |

**Quick start**

Get the code:

```bash
git clone https://github.com/AnupamGupta86/InmarCodingAssignment.git && cd InmarCodingAssignment
```

*Approach 1: command line* (needs Python 3.14.8; details in [5.2](#52-get-the-code-and-run-the-tests) and [5.3](#53-validate-the-use-cases-with-the-command-line)):

```bash
python3 -m unittest discover -s atm-dispenser        # 211 tests
python3 atm-dispenser/src/cli.py 2700                # example from the brief
```

*Approach 2: HTTP service in Docker* (needs Docker, plus Python 3.14.8 because the script runs the tests
first; Docker-only commands and details in [5.4](#54-build-validate-and-run-the-service-in-a-container)):

```bash
./scripts/run-container.sh                           # test, build, validate and run the service
export ATM_API_KEY="$(docker exec atm-dispenser printenv ATM_API_KEY)"
curl -s -H "Authorization: Bearer $ATM_API_KEY" -H "Content-Type: application/json" \
     -d '{"amount": 2700}' http://127.0.0.1:8080/withdrawals                # example from the brief
./scripts/run-container.sh --stop                    # stop and remove the container
```

### 5.1 Prerequisites

| Needed for | Tool | Check |
|---|---|---|
| Tests and CLI ([5.2](#52-get-the-code-and-run-the-tests), [5.3](#53-validate-the-use-cases-with-the-command-line)) | Python 3.14.8 | `python3 --version` |
| Service in a container ([5.4](#54-build-validate-and-run-the-service-in-a-container)) | Docker | `docker version` |
| Optional dev checks ([5.5](#55-optional-quality-and-security-checks)) | ruff, mypy, coverage, pip-audit in a virtual environment (`.venv`, set up in [5.5](#55-optional-quality-and-security-checks)) | `source .venv/bin/activate && ruff --version` |

### 5.2 Get The Code And Run The Tests

```bash
git clone https://github.com/AnupamGupta86/InmarCodingAssignment.git
cd InmarCodingAssignment
python3 -m unittest discover -s atm-dispenser -v        # expect: Ran 211 tests ... OK
```

Nothing to install and no `PYTHONPATH` to set: the scripts find their own modules, and the tests
add `src/` to the path themselves.

### 5.3 Validate The Use Cases With The Command Line

| # | Use case | Command | Expected result |
|---|---|---|---|
| 1 | Example from the brief | `python3 atm-dispenser/src/cli.py 2700` | `₹500 × 5, ₹200 × 1 (Total notes dispensed: 6)`; updated inventory `₹500 × 3, ₹200 × 9, ₹100 × 20`; exit code 0 |
| 2 | Greedy would fail | `python3 atm-dispenser/src/cli.py --inventory "4:1, 3:2" 6` | `₹3 × 2` |
| 3 | Limited notes | `python3 atm-dispenser/src/cli.py --inventory "500:2, 200:10, 100:20" 2400` | `₹500 × 2, ₹200 × 7` |
| 4 | Cannot dispense | `python3 atm-dispenser/src/cli.py 250` | `Cannot dispense requested amount`; `Inventory unchanged`; exit code 1 |
| 5 | More than the ATM holds | `python3 atm-dispenser/src/cli.py 9000` | `Cannot dispense requested amount`; exit code 1 |
| 6 | Several withdrawals in a row | `python3 atm-dispenser/src/cli.py 2700 2700 800` | the inventory goes down after each one |
| 7 | Zero / negative / text / decimal | `python3 atm-dispenser/src/cli.py 0`, `… -- -5`, `… abc`, `… 12.5` | clear error message; exit code 2 |
| 8 | Invalid inventory | `python3 atm-dispenser/src/cli.py --inventory "{}" 100`, `--inventory "500:-1"`, `--inventory "0:5"` | `empty`, `cannot be negative`, `Invalid denomination` |
| 9 | Above the withdrawal limit | `python3 atm-dispenser/src/cli.py --max-withdrawal 1000 2700` | `exceeds the limit of 1000`; exit code 2 |
| 10 | Interactive | `python3 atm-dispenser/src/cli.py`, then type amounts, `q` (or Ctrl+C) to quit | one result per line |
| 11 | Check the inventory (interactive) | `python3 atm-dispenser/src/cli.py`, type `2700`, then `i` | `ATM inventory: ₹500 × 3, ₹200 × 9, ₹100 × 20 (total cash ₹5300)`; nothing withdrawn |
| 12 | Inventory kept between runs | `python3 atm-dispenser/src/cli.py --state-file /tmp/atm.json 2700`, then the same command again | second run starts from `₹500 × 3, ₹200 × 9, ₹100 × 20`; delete `/tmp/atm.json` to start fresh |
| 13 | Several processes share one ATM | `./scripts/shared-inventory-test.sh` (or `make shared-inventory`) | without a state file 10 processes pay 20 notes from 8; with one, exactly 4 are paid and 0 notes remain |

Check an exit code with `echo $?` straight after the command.

### 5.4 Build, Validate And Run The Service In A Container

One command does every step:

```bash
export ATM_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"   # optional
./scripts/run-container.sh          # or: make container
```

| Step | What it does | Pass condition |
|---|---|---|
| 1 | Runs the 211 unit, integration and security tests | `OK` (otherwise nothing is built) |
| 2 | Builds `atm-dispenser:<version>`, e.g. `atm-dispenser:1.0.20261120183012` | build succeeds |
| 3 | Validates the image (`scripts/validate-image.sh`) | version label and `ATM_VERSION` match; user 10001; health check defined; port 8080 exposed; `/app` holds only the application modules; no pip or setuptools; size ≤ 250 MB; `/data` writable for the state file; the CLI inside dispenses the brief's example |
| 4 | Starts container `atm-dispenser` on `127.0.0.1:8080`: read-only filesystem, all capabilities dropped, `no-new-privileges`, 128 MB memory, 0.5 CPU, 100 processes | container running |
| 5 | Waits for the Docker health check (`GET /health`) | `healthy` within 30 s |
| 6 | Hardening checks inside the running container | runs as user 10001 (not root); writing a file fails |
| 7 | End-to-end smoke test with the API key | `All checks passed`, including "service reports version <version>" |

Then try the service:

```bash
export ATM_API_KEY="$(docker exec atm-dispenser printenv ATM_API_KEY)"
AUTH="Authorization: Bearer $ATM_API_KEY"; JSON="Content-Type: application/json"; URL=http://127.0.0.1:8080

curl -s $URL/health                                                     # shows the running version
curl -s -H "$AUTH" $URL/inventory
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": 2700}' $URL/withdrawals   # 200: 500×5, 200×1
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": 250}'  $URL/withdrawals   # 422: Cannot dispense requested amount
curl -s -H "$AUTH" -H "$JSON" -d '{"amount": 0}'    $URL/withdrawals   # 400: Amount must be greater than zero
curl -s -H "$JSON" -d '{"amount": 100}' $URL/withdrawals                # 401: no API key

docker logs -f atm-dispenser        # JSON request logs (Ctrl+C to stop following)
./scripts/run-container.sh --stop   # stop and remove the container (or: make container-stop)
```

**Keep the inventory across container restarts** (a named volume holds the state file):

```bash
./scripts/run-container.sh --stop        # free the name if the container from above is still running
docker run -d --name atm-dispenser -p 127.0.0.1:8080:8080 --read-only --cap-drop ALL \
  -v atm-data:/data -e ATM_STATE_FILE=/data/atm.json atm-dispenser:local
```

**Windows (PowerShell) or any machine with only Docker:**

```bash
docker build -t atm-dispenser .
docker run --rm atm-dispenser python3 cli.py 2700                           # the CLI in the image
docker run -d --name atm-dispenser -p 127.0.0.1:8080:8080 --read-only --cap-drop ALL atm-dispenser
curl http://127.0.0.1:8080/health
docker rm -f atm-dispenser
```

### 5.5 Optional Quality And Security Checks

The dev tools go in a virtual environment (Homebrew and recent Linux Pythons refuse system-wide
`pip install` with `error: externally-managed-environment`):

```bash
python3 -m venv .venv && source .venv/bin/activate       # once; later just: source .venv/bin/activate
python3 -m pip install -r atm-dispenser/requirements-dev.txt
make lint coverage security            # ruff (incl. security rules) + mypy, coverage ≥ 70 %, pip-audit
deactivate                             # leave the virtual environment
```

The image, Dockerfile, secret and DAST scans run in CI (see [Security & DevSecOps](#7-security--devsecops)).

### 5.6 Command-Line Reference

| What | Command |
|---|---|
| One withdrawal (default inventory from the brief) | `python3 atm-dispenser/src/cli.py 2700` |
| Your own inventory | `python3 atm-dispenser/src/cli.py --inventory "500:8, 200:10, 100:20" 2700` |
| The brief's format also works | `python3 atm-dispenser/src/cli.py --inventory "{ 500: 8, 200: 10, 100: 20 }" 2700` |
| Several withdrawals on the same ATM, in order | `python3 atm-dispenser/src/cli.py 2700 2700 800` |
| Interactive mode (type amounts, `q` to quit) | `python3 atm-dispenser/src/cli.py` |
| Show the current inventory and total cash (interactive mode) | type `i` or `inventory` |
| Show the current inventory (service) | `curl -s -H "Authorization: Bearer $ATM_API_KEY" http://127.0.0.1:8080/inventory` |
| Keep the inventory in a JSON file (survives restarts, shared by processes) | `python3 atm-dispenser/src/cli.py --state-file atm.json 2700` |
| A negative amount (`--` stops it being read as an option) | `python3 atm-dispenser/src/cli.py -- -5` |
| Change the per-withdrawal limit (default 200000) | `python3 atm-dispenser/src/cli.py --max-withdrawal 500000 300000` |
| Help | `python3 atm-dispenser/src/cli.py --help` |
| The same inside Docker | `docker run --rm atm-dispenser python3 cli.py 2700` |

**Exit codes** (with several amounts the worst one wins):

| Code | Meaning |
|---|---|
| `0` | Every withdrawal was dispensed |
| `1` | At least one amount could not be dispensed ("Cannot dispense requested amount") |
| `2` | Invalid input (amount, inventory or limit), or a state file that cannot be read or saved |

### 5.7 HTTP Service Reference

The service runs in the Docker image (section [5.4](#54-build-validate-and-run-the-service-in-a-container)).

| Method & path | Purpose | Responses |
|---|---|---|
| `GET /health` | Liveness probe and build version | `200 {"status": "ok", "version": "1.0.20261120183012"}` |
| `GET /inventory` | Notes in the ATM | `200 {"inventory": {"500": 8, …}, "total_cash": 8000}` |
| `POST /withdrawals` | Withdraw `{"amount": 2700}` | `200` dispensed · `400` invalid input · `422` cannot dispense · `401` no/invalid API key · `408` body too slow · `413` body > 1 KB · `415` not JSON · `503` state file could not be saved, or too many connections |
| `GET /openapi.json` | OpenAPI 3 contract (used by the OWASP ZAP scan in CI) | `200` |

```bash
$ curl -s -H 'Content-Type: application/json' -d '{"amount": 2700}' localhost:8080/withdrawals
{"amount": 2700, "dispensed": {"500": 5, "200": 1}, "total_notes": 6, "inventory": {"500": 3, "200": 9, "100": 20}}

$ curl -s -H 'Content-Type: application/json' -d '{"amount": 250}' localhost:8080/withdrawals   # HTTP 422
{"error": "Cannot dispense requested amount", "amount": 250, "inventory": {"500": 3, "200": 9, "100": 20}}
```

JSON object keys are always text, so denominations appear as `"500"`. The `inventory` in each
response is the state right after that withdrawal, even when other customers withdraw at the same time.

Configuration (environment variables, e.g. `docker run -e ATM_INVENTORY="4:1, 3:2" ...`):

| Variable | Default | Meaning |
|---|---|---|
| `ATM_INVENTORY` | `500:8, 200:10, 100:20` | Notes in the ATM |
| `ATM_HOST` | `0.0.0.0` in the image | Listen address |
| `ATM_PORT` | `8080` | Listen port |
| `ATM_MAX_WITHDRAWAL` | `200000` | Largest amount per withdrawal (like a real ATM limit; also caps the work per request) |
| `ATM_API_KEY` | not set | If set (16+ chars), `/inventory` and `/withdrawals` need `Authorization: Bearer <key>` |
| `ATM_STATE_FILE` | not set (memory only) | JSON file for the inventory, e.g. `/data/atm.json` on a volume; an existing file wins over `ATM_INVENTORY` |
| `ATM_VERSION` | the build version | Version reported by `/health` |

Invalid configuration stops the service at start-up with a clear message (exit code 2). Logs are
one JSON object per line on stdout. `SIGTERM` (`docker stop`) shuts the service down cleanly.

### 5.8 Makefile Shortcuts

| Target | Does |
|---|---|
| `make test` | all automated tests |
| `make version` | print the build version |
| `make docker-build` | tests, then build and validate the versioned image |
| `make shared-inventory` | `scripts/shared-inventory-test.sh` |
| `make container` / `make container-stop` | `scripts/run-container.sh` / stop it |
| `make smoke` | smoke test a running service (`URL=...`) |
| `make lint coverage security` | dev checks (inside the virtual environment from [5.5](#55-optional-quality-and-security-checks)) |

---

## 6. Validation & Testing Strategy

### 6.1 Validation Rules And Messages

| Input | Result |
|---|---|
| `0` | `Amount must be greater than zero` |
| `-500` | `Amount cannot be negative, got -500` |
| `"abc"`, `""`, `None`, `True`, `"2_700"`, `"१००"`, `"１００"` (non-ASCII digits) | `Amount must be a number, got …` |
| `12.5`, `"12.5"`, `2700.0` | `Amount must be a whole number, got …` |
| Text longer than 18 digits | `Amount is too large: at most 18 digits` |
| Above the per-withdrawal limit (default 200000) | `Amount … exceeds the limit of 200000 per withdrawal` |
| `{}` | `ATM inventory is empty: add at least one denomination` |
| `{500: -1}` | `… note count … cannot be negative` |
| `{500: 2.5}`, `{500: "8"}` | `… note count … must be a whole number` |
| `{0: 5}`, `{-100: 5}`, `{0.5: 5}`, `{"500": 5}` | `Invalid denomination … must be a positive whole number` |
| More than 50 denominations | `Too many denominations: N (at most 50)` |
| An amount that would need more than 64 million calculation steps (only possible after raising the withdrawal limit, e.g. ₹10¹² in ₹1 notes) | `Amount … is too large to work out with this inventory; lower the amount or the withdrawal limit` |
| `{"amount": 100, "amount": 2700}` (service) | `Duplicate field(s): amount` (`400`; neither value is used) |
| State file that is not valid JSON, has the wrong shape, bad values or the same denomination twice (`"500"` and `"0500"`) | `Cannot read the state file …` / `Invalid denomination … in the state file` (CLI exit code 2; the service does not start) |
| State file in a folder that does not exist or is not writable | `Cannot use the state file …` (CLI exit code 2; the service does not start) |
| State file can no longer be saved while running (e.g. the folder became read-only) | `Cannot save the state file …; nothing was dispensed` (CLI exit code 2, service `503`); the inventory is unchanged |
| Amount that cannot be made exactly (e.g. 250) | `Cannot dispense requested amount` (CLI exit code 1, service `422`) |

Amount errors are `InvalidAmountError` and inventory errors `InvalidInventoryError`, both
subclasses of `ATMError`. Decisions worth calling out:

- Amounts may be whole numbers or whole-number text (`"2700"`, `" 2700 "`), as a keypad or command
  line produces. Floats are rejected even when whole (`2700.0`): money never passes through floating point.
- `True`/`False` are rejected although Python treats them as 1/0.
- Only the ASCII digits 0–9 count: Python itself would also accept Arabic-Indic, Devanagari or
  full-width digits, which no ATM keypad sends.
- Error messages never repeat long input in full: values are cut to 30 characters.
- A state file saved with a byte-order mark (Windows Notepad) is accepted, and a state file that is
  a symbolic link stays a link: saves update the file it points to.
- A denomination with **0 notes is allowed** (an empty cassette); if every count is 0, every
  withdrawal gives "Cannot dispense".

### 6.2 Testing Strategy

```bash
python3 -m unittest discover -s atm-dispenser -v                       # all 211 tests, about 6 seconds
python3 -m unittest discover -s atm-dispenser -p test_service.py       # one test file (test_dispenser / test_cli / test_service)
python3 atm-dispenser/tests/e2e/smoke_test.py http://localhost:8080    # against a running service
```

| Type | Where | What it proves |
|---|---|---|
| **Unit** | `test_dispenser.py`, `test_cli.py` | Every rule of the brief: validation messages, minimum notes, limited notes, greedy traps, "cannot dispense", inventory updated only on success, withdrawal limit, inventory cannot be changed from outside |
| **Correctness oracle** | `test_dispenser.AgainstBruteForceTests` | 400 random ATMs: the result always equals an exhaustive search of every combination |
| **Property / fuzz** | `test_dispenser.ValidationFuzzTests`, `DispenserPropertyTests`, `test_service.HttpFuzzTests` | Thousands of random and hostile inputs (unicode digits, NaN, huge numbers, random bytes): validation only returns clean values or our own error; invariants hold over random withdrawal sequences; the HTTP service never answers 500 |
| **Concurrency** | `test_dispenser.ConcurrentWithdrawalTests`, `test_service.ConcurrentHttpTests` | 20 threads competing for 8 notes; a deliberately slowed `NoteAllocator` to force a race; a **control test proving the race happens without the lock**; 25 parallel HTTP withdrawals; each response reports the inventory right after its own withdrawal |
| **State file (persistence)** | `test_dispenser.StateFileTests`, `test_cli.StateFileTests`, `test_service.ConfigTests` | Created from the starting inventory; survives restarts; existing file wins; failed withdrawal or failed save changes nothing; no temporary files left; corrupt files rejected; **two ATMs sharing one file never pay the same notes** (and the test fails if the file lock is removed) |
| **Several processes** | `scripts/shared-inventory-test.sh` (CI) | 10 real `python3` processes at once: without a state file they pay 20 notes from 8; with a shared one exactly 4 are paid |
| **Adversarial regression** | `test_dispenser.HostileInputRegressionTests`, `test_service.HostileHttpRegressionTests`, `StateFileTests`, `test_cli.StateFileTests` | One test per problem found by trying to break the code: huge calculations refused (no `MemoryError`), too many denominations, long input not echoed, duplicate JSON fields, conflicting `Content-Length`, failed save (CLI message, HTTP `503`), connection cap (`503`), duplicate denominations, byte-order mark and symbolic link in the state file |
| **Integration (HTTP)** | `test_service.py` | A real server on a free port: every endpoint, status code and error path; keep-alive; malformed HTTP; slow (408) and cut-off (400) bodies; version in `/health`; start-up and clean shutdown on SIGTERM |
| **Security** | `test_service.SecurityTests`, `HttpProtocolTests` | API key (missing, wrong, wrong scheme), security headers, no version disclosure, no stack-trace leaks, request-id sanitising, body size/content-type limits, path traversal, logs without request bodies, a request hidden in a GET body cannot be smuggled through |
| **Configuration** | `test_service.ConfigTests` | Every environment variable, and that bad values stop the service from starting |
| **CLI** | `test_cli.py` | Output format, exit codes, withdrawal limit, interactive mode, the `i` inventory command and Ctrl+C, `python3 atm-dispenser/src/cli.py` end to end |
| **Performance** | `test_dispenser.PerformanceTests` | ₹199,990 from a large ATM in well under a second; a trillion notes per denomination is no slower |
| **Smoke-test script** | `test_service.SmokeScriptTests` | The e2e script passes against a healthy service, with and without an API key, checks the expected version, and fails clearly (not a crash) when the key is missing |
| **Image validation** | `scripts/validate-image.sh` | Version label and env, non-root user, health check, exposed port, only app code inside, no pip or setuptools, size limit, `/data` writable, CLI works inside the image |
| **Container** | `scripts/run-container.sh`, CI | Health check passes, runs as non-root user 10001, filesystem is read-only |
| **End-to-end smoke** | `tests/e2e/smoke_test.py` | Against the running container: health and version, headers, withdraw, inventory change, failure leaves inventory unchanged, every validation error, 404/405/415, 401 without key |
| **DAST** | CI (OWASP ZAP) | Active scan of the running container, driven by `/openapi.json` |

---

## 7. Security & DevSecOps

### 7.1 Security Controls

| Area | Control |
|---|---|
| Input | Every amount and inventory value validated; JSON only; body ≤ 1 KB; unknown fields rejected; numeric text ≤ 18 ASCII digits; per-withdrawal limit caps the work a request can cause |
| HTTP protocol | Conflicting `Content-Length` headers and duplicate JSON fields are rejected (no request smuggling or ambiguous amounts); a body the server does not read closes the connection, so it can never be read as a hidden second request; slow bodies get 408, cut-off bodies 400 |
| Authentication | Optional bearer API key (`ATM_API_KEY`, ≥ 16 chars), compared in constant time; passed to the container from the environment, never stored in Git or written on a command line |
| Errors | No stack traces or internal details in responses; JSON errors for malformed HTTP too |
| HTTP headers | `nosniff`, `X-Frame-Options: DENY`, restrictive CSP, `no-referrer`, `no-store`; server software version hidden |
| Availability | Socket timeout against slow clients; connection backlog sized for bursts; at most 64 connections at once (more get `503` + `Retry-After`); calculation capped at 64 million steps and 50 denominations, so no request can exhaust memory or CPU; CPU, memory and process limits on the container |
| State file | Optional; written atomically (temporary file + rename, fsync); validated like any input when read; lives on a volume, so the container filesystem stays read-only |
| Logging | Structured JSON logs; request bodies never logged; client request ids sanitised against log injection |
| Container | Slim base with OS packages patched at build time, no pip or setuptools (nothing to install at runtime), application code only, non-root UID 10001, read-only filesystem, all capabilities dropped, `no-new-privileges`, health check; validated before every run |
| Supply chain | Standard library only (no runtime dependencies), versioned images, SBOM per build, pinned tool versions |

### 7.2 Automated Security Checks

| Check | Tool | Fails the build on |
|---|---|---|
| Secret scanning (full history) | gitleaks | any secret |
| SAST | ruff `S` rules (the Bandit rule set), CodeQL (`security-extended`) | any finding |
| Dependency audit | pip-audit | known vulnerable version |
| Dockerfile lint | hadolint | warning or worse |
| Image validation | `scripts/validate-image.sh` | any failed check |
| Image CVEs | Trivy | fixable HIGH/CRITICAL |
| Dockerfile misconfiguration | Trivy config | HIGH/CRITICAL |
| Running container | health check, non-root and read-only checks, smoke tests | any failure |
| DAST against the running container | OWASP ZAP API scan (driven by `/openapi.json`) | report published as an artifact |

### 7.3 CI/CD Pipeline

Every push runs `.github/workflows/ci.yml`; `azure-pipelines.yml` has the same stages for Azure DevOps.
Python is pinned to **3.14.8** everywhere: CI, Azure DevOps and the Docker base image.

```
 quality ─────────────────┐   ruff (incl. the Bandit security rules), mypy, pip-audit
 codeql                   │   CodeQL security analysis (security-extended queries)
 secrets                  │   gitleaks over the full git history
 test (Linux, macOS)      │   unit, integration, concurrency, fuzz, CLI, shared-inventory processes
 coverage                 │   fails below 70 %
                          ▼   (the image is built only if all of the above pass)
 container            version (major.minor.datetime) → hadolint → build → validate image
                      → Trivy image CVEs → Trivy Dockerfile scan → SBOM
                      → start hardened container → health check → non-root & read-only checks
                      → smoke test (checks the version) → smoke test with API key → OWASP ZAP DAST
```

- **Versioning:** each run computes `major.minor.datetime` (IST) with `scripts/version.sh`, tags the
  image with it, shows it in the GitHub job summary, and sets it as the Azure DevOps build number.
  The SBOM artifact is named after the version.
- **Image validation** (`scripts/validate-image.sh`) runs before any scan or container start:
  version label and `ATM_VERSION`, user 10001, health check, port 8080, only the application modules in
  `/app`, no pip or setuptools, size ≤ 250 MB, `/data` writable, and the CLI inside the image dispenses the brief's example.
- **Image patching:** the Dockerfile upgrades the OS packages at build time, so fixes released
  after the base image was published are included, and it removes pip and setuptools, which the
  app never uses. Trivy then fails the build on any remaining fixable HIGH or CRITICAL vulnerability.
- **Platforms:** tests run on Linux and macOS with `python3`; Windows is covered by the Docker image.
- The pipeline also runs every Monday, so newly published vulnerabilities in the code or the
  base image are found even without a push.

---

## 8. Troubleshooting & FAQ

### 8.1 Troubleshooting

| Symptom | Fix |
|---|---|
| `python3: command not found` (Windows) | Use the Docker commands in [5.4](#54-build-validate-and-run-the-service-in-a-container), or WSL |
| `Address already in use` | Another process uses port 8080: `PORT=8081 ./scripts/run-container.sh`, or stop it (`lsof -i :8080`) |
| `401 Missing or invalid API key` | Send `Authorization: Bearer <key>` with the container's key (`docker exec atm-dispenser printenv ATM_API_KEY`) |
| `415 Content-Type must be application/json` | Add `-H 'Content-Type: application/json'` |
| Image validation fails | The failing check is printed; rebuild with `--build-arg VERSION=...` and check the Dockerfile |
| Container not `healthy` | `docker logs atm-dispenser`; check port 8080 is free; `docker inspect atm-dispenser` shows the health-check output |
| `quote>` prompt or `command not found: #` after pasting a command (macOS zsh) | zsh does not ignore `# comments` by default: press Ctrl+C, run `setopt interactivecomments` (add it to `~/.zshrc` to keep it), paste again |
| `Cannot connect to the Docker daemon` | Start Docker Desktop and try again |
| `Conflict. The container name "/atm-dispenser" is already in use` | A container with that name is still running (e.g. from `run-container.sh`): `./scripts/run-container.sh --stop` or `docker rm -f atm-dispenser`, then run again |
| `Cannot use the state file` / `Cannot read the state file` | The folder must exist and be writable; delete a corrupt file to start again from `--inventory` / `ATM_INVENTORY` |
| Version check fails: time zone `Asia/Kolkata` not available | Install the time zone data (`tzdata`) on the build machine |

### 8.2 Frequently Asked Questions

**Q1. Why does the CLI start from the full inventory every time I run it?**

Each run is a new ATM held in memory, so `python3 atm-dispenser/src/cli.py 2700` always reproduces
the brief's example. To watch the inventory go down:

```bash
python3 atm-dispenser/src/cli.py 2700 2700 800                    # several withdrawals in one run
python3 atm-dispenser/src/cli.py                                  # interactive: type amounts, i = inventory, q = quit
python3 atm-dispenser/src/cli.py --state-file atm.json 2700       # kept between runs in atm.json
```

**Q2. With `--inventory "500:2, 200:10, 100:20"`, why is ₹2400 paid as ₹500 × 2 + ₹200 × 7, not ₹500 × 4 + ₹200 × 2?**

- That ATM holds only **two** ₹500 notes, and the brief forbids dispensing notes that are not there.
- With two ₹500 notes, the fewest possible is 9 notes: ₹500 × 2 + ₹200 × 7.
- With the default inventory (8 × ₹500), ₹2400 is paid as ₹500 × 4 + ₹200 × 2 (6 notes).

**Q3. Where is the exit code?**

Programs return it to the shell instead of printing it. Check it straight after the command:

```bash
python3 atm-dispenser/src/cli.py 9000
echo $?        # 1
```

| Code | Meaning |
|---|---|
| `0` | Dispensed |
| `1` | Cannot dispense requested amount |
| `2` | Invalid input, or a state file that cannot be read or saved |

**Q4. Are only ₹500, ₹200 and ₹100 supported?**

No. Any positive whole-number denominations work; ₹500/₹200/₹100 is only the default from the brief.

```bash
python3 atm-dispenser/src/cli.py --inventory "2000:2, 50:4, 7:3" 2064    # ₹2000 × 1, ₹50 × 1, ₹7 × 2
```

For the service, set `ATM_INVENTORY` the same way.

**Q5. What is the GCD?**

- GCD = **Greatest Common Divisor** of the denominations: 100 for ₹500/₹200/₹100.
- Every payable amount is a multiple of it, so ₹2700 is worked out as 27 units and ₹250 is
  rejected at once.
- See [2.3.2](#232-dynamic-programming-bounded-coin-change).

**Q6. Can the withdrawal limit be changed?**

Yes. The default is 200000 per withdrawal.

| Where | How |
|---|---|
| Command line | `--max-withdrawal 500000` |
| Service | `ATM_MAX_WITHDRAWAL=500000` |

**Q7. How is the ATM refilled?**

- Restart it with the new counts: `--inventory` / `ATM_INVENTORY`, or replace the state file while it is stopped.
- There is deliberately **no refill API**: it would let anyone with the key add cash that is not
  physically in the machine.
