"""The ATM as a small HTTP/JSON service (standard library only).

    python -m atm.service                     # listens on http://127.0.0.1:8080

Endpoints
    GET  /health        liveness probe   -> 200 {"status": "ok"}
    GET  /ready         readiness probe  -> 200 {"status": "ready"}
    GET  /inventory     notes in the ATM -> 200 {"inventory": {"500": 8, ...}, "total_cash": 8000}
    GET  /openapi.json  OpenAPI 3 description of this API
    POST /withdrawals   body {"amount": 2700}
                        200 dispensed         {"amount", "dispensed", "total_notes", "inventory"}
                        400 invalid input     {"error": "Amount cannot be negative, got -5"}
                        422 cannot dispense   {"error": "Cannot dispense requested amount", ...}

    JSON object keys are always text, so denominations appear as "500" rather than 500.

Configuration (environment variables)
    ATM_INVENTORY        notes in the ATM, e.g. "500:8, 200:10, 100:20" (default: the brief's)
    ATM_HOST             address to listen on (default 127.0.0.1; containers set 0.0.0.0)
    ATM_PORT             port (default 8080)
    ATM_MAX_WITHDRAWAL   largest amount per withdrawal (default 200000)
    ATM_API_KEY          if set (16+ characters), /inventory and /withdrawals need the header
                         "Authorization: Bearer <key>". Health checks stay open.

Security controls are listed in the "Security" section of the README.
"""

import hmac
import json
import logging
import os
import re
import signal
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlsplit

from atm.cli import DEFAULT_INVENTORY, parse_inventory
from atm.dispenser import CANNOT_DISPENSE, CashDispenser
from atm.errors import ATMError
from atm.openapi import OPENAPI_SPEC
from atm.validation import validate_amount

MAX_BODY_BYTES = 1024
DEFAULT_MAX_WITHDRAWAL = 200_000
MIN_API_KEY_LENGTH = 16

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

log = logging.getLogger("atm.service")


class ConfigError(ValueError):
    """The service cannot start because an environment variable is wrong."""



def load_config(environ: Mapping[str, str]) -> Dict[str, Any]:
    """Read and check the settings from environment variables."""
    port_text = environ.get("ATM_PORT", "8080")
    if not port_text.isdigit() or not 0 <= int(port_text) <= 65535:
        raise ConfigError(f"ATM_PORT must be a number from 0 to 65535, got {port_text!r}")

    try:
        max_withdrawal = validate_amount(environ.get("ATM_MAX_WITHDRAWAL", str(DEFAULT_MAX_WITHDRAWAL)))
    except ATMError as error:
        raise ConfigError(f"ATM_MAX_WITHDRAWAL is invalid: {error}") from None

    api_key = environ.get("ATM_API_KEY") or None
    if api_key is not None and len(api_key) < MIN_API_KEY_LENGTH:
        raise ConfigError(f"ATM_API_KEY must be at least {MIN_API_KEY_LENGTH} characters long")

    try:
        atm = CashDispenser(parse_inventory(environ.get("ATM_INVENTORY", DEFAULT_INVENTORY)),
                            max_withdrawal=max_withdrawal)
    except ATMError as error:
        raise ConfigError(f"ATM_INVENTORY is invalid: {error}") from None

    return {
        "atm": atm,
        "host": environ.get("ATM_HOST", "127.0.0.1"),
        "port": int(port_text),
        "api_key": api_key,
    }


# --------------------------------------------------------------------------- HTTP handling

def _as_json_notes(notes: Mapping[int, int]) -> Dict[str, int]:
    return {str(value): count for value, count in notes.items()}


class ATMRequestHandler(BaseHTTPRequestHandler):
    """Handles one HTTP request. `self.server` is the ATMServer that owns the ATM."""

    server: "ATMServer"
    server_version = "atm-service"   # do not advertise the Python version
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 10                      # seconds a client may stay silent before we hang up


    GET_ROUTES = ("/health", "/ready", "/inventory", "/openapi.json")
    POST_ROUTES = ("/withdrawals",)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        self._started = time.perf_counter()
        incoming_id = self.headers.get("X-Request-ID", "")
        self._request_id = incoming_id if _SAFE_REQUEST_ID.match(incoming_id) else uuid.uuid4().hex
        route = urlsplit(self.path).path.rstrip("/") or "/"
        try:
            if method == "GET" and route == "/health":
                self._send(HTTPStatus.OK, {"status": "ok"})
            elif method == "GET" and route == "/ready":
                self._send(HTTPStatus.OK, {"status": "ready"})
            elif method == "GET" and route == "/openapi.json":
                self._send(HTTPStatus.OK, OPENAPI_SPEC)
            elif method == "GET" and route == "/inventory":
                if self._authorised():
                    self._send(HTTPStatus.OK, self._inventory_body())
            elif method == "POST" and route == "/withdrawals":
                if self._authorised():
                    self._withdraw()
            elif route in self.GET_ROUTES or route in self.POST_ROUTES:
                allowed = "GET" if route in self.GET_ROUTES else "POST"
                self._send(HTTPStatus.METHOD_NOT_ALLOWED,
                           {"error": f"Method {method} not allowed; use {allowed}"},
                           extra_headers={"Allow": allowed}, close=True)
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"}, close=True)
        except Exception:  # never show a stack trace to the client
            log.exception("unexpected error", extra={"request_id": self._request_id})
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"}, close=True)


    def _inventory_body(self) -> Dict[str, Any]:
        atm = self.server.atm
        return {"inventory": _as_json_notes(atm.inventory), "total_cash": atm.total_cash}

    def _withdraw(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        unknown = sorted(set(payload) - {"amount"})
        if unknown:
            self._send(HTTPStatus.BAD_REQUEST, {"error": f"Unknown field(s): {', '.join(unknown)}"})
            return
        if "amount" not in payload:
            self._send(HTTPStatus.BAD_REQUEST, {"error": 'Missing field "amount"'})
            return

        atm = self.server.atm
        try:
            amount = validate_amount(payload["amount"])
            notes = atm.dispense(amount)
        except ATMError as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        if notes is None:
            self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {
                "error": CANNOT_DISPENSE,
                "amount": amount,
                "inventory": _as_json_notes(atm.inventory),
            })
            return

        self._send(HTTPStatus.OK, {
            "amount": amount,
            "dispensed": _as_json_notes(notes),
            "total_notes": sum(notes.values()),
            "inventory": _as_json_notes(atm.inventory),
        })


    def _authorised(self) -> bool:
        expected = self.server.api_key
        if expected is None:
            return True
        given = self.headers.get("Authorization", "")
        scheme, _, token = given.partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(token.encode(), expected.encode()):
            return True
        # The body (if any) is not read, so the connection must be closed afterwards.
        self._send(HTTPStatus.UNAUTHORIZED, {"error": "Missing or invalid API key"},
                   extra_headers={"WWW-Authenticate": "Bearer"}, close=True)
        return False

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        """Return the request body as a dict, or send an error response and return None."""
        if self.headers.get("Transfer-Encoding"):
            self._send(HTTPStatus.LENGTH_REQUIRED,
                       {"error": "Chunked bodies are not supported; send Content-Length"}, close=True)
            return None
        length_text = self.headers.get("Content-Length")
        if length_text is None:
            self._send(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length header is required"}, close=True)
            return None
        if not length_text.strip().isdigit():
            self._send(HTTPStatus.BAD_REQUEST, {"error": "Invalid Content-Length header"}, close=True)
            return None
        length = int(length_text)
        if length > MAX_BODY_BYTES:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                       {"error": f"Request body is larger than {MAX_BODY_BYTES} bytes"}, close=True)
            return None

        raw = self.rfile.read(length)  # read it even if we reject it, to keep the connection in sync

        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            self._send(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Content-Type must be application/json"})
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "Request body must be valid JSON"})
            return None
        if not isinstance(payload, dict):
            self._send(HTTPStatus.BAD_REQUEST, {"error": 'Request body must be a JSON object like {"amount": 2700}'})
            return None
        return payload

    def _send(self, status: HTTPStatus, body: Dict[str, Any],
              extra_headers: Optional[Dict[str, str]] = None, close: bool = False) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Request-ID", getattr(self, "_request_id", "-"))
        for name, value in {**SECURITY_HEADERS, **(extra_headers or {})}.items():
            self.send_header(name, value)
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        self._log_request(int(status))

    def send_error(self, code: int, message: Optional[str] = None, explain: Optional[str] = None) -> None:
        """Malformed requests (bad request line, header too long, ...) also get a JSON answer."""
        if self.request_version == "HTTP/0.9":  # older Pythons would otherwise omit the status line
            self.request_version = "HTTP/1.1"
        self._send(HTTPStatus(code), {"error": message or HTTPStatus(code).phrase}, close=True)

    def _log_request(self, status: int) -> None:
        started = getattr(self, "_started", None)
        log.info("request", extra={
            "request_id": getattr(self, "_request_id", "-"),
            "method": getattr(self, "command", None),
            "path": urlsplit(getattr(self, "path", "") or "").path[:200],
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2) if started else None,
            "client": self.client_address[0] if self.client_address else None,
        })

    def version_string(self) -> str:
        return self.server_version

    def log_message(self, format: str, *args: Any) -> None:
        """Replace the default unstructured stderr logging (it would print raw request lines)."""


class ATMServer(ThreadingHTTPServer):
    """An HTTP server that owns one ATM. Each request runs in its own thread; the ATM's lock
    keeps concurrent withdrawals from taking the same notes."""

    daemon_threads = True
    request_queue_size = 128  # the default (5) drops connections when many customers arrive at once

    def __init__(self, address, atm: CashDispenser, api_key: Optional[str] = None):
        super().__init__(address, ATMRequestHandler)
        self.atm = atm
        self.api_key = api_key


def create_server(atm: CashDispenser, host: str = "127.0.0.1", port: int = 8080,
                  api_key: Optional[str] = None) -> ATMServer:
    """Create (but do not start) the server. Port 0 picks a free port (used by the tests)."""
    return ATMServer((host, port), atm, api_key)



class JsonLogFormatter(logging.Formatter):
    """One JSON object per line: easy to search in any log platform."""

    FIELDS = ("request_id", "method", "path", "status", "duration_ms", "client")

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        entry.update({f: getattr(record, f) for f in self.FIELDS if hasattr(record, f)})
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def main(environ: Mapping[str, str] = os.environ) -> int:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)

    try:
        config = load_config(environ)
        server = create_server(config["atm"], config["host"], config["port"], config["api_key"])
    except (ConfigError, OSError) as error:
        log.error(f"cannot start: {error}")
        return 2

    if config["api_key"] is None:
        log.warning("ATM_API_KEY is not set: /inventory and /withdrawals are open to anyone")

    def stop(signum, frame):  # docker stop sends SIGTERM
        log.info("shutting down")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    log.info(f"listening on http://{config['host']}:{server.server_port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
