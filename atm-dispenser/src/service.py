"""The ATM as a small, hardened HTTP/JSON service (standard library only).

    python3 atm-dispenser/src/service.py            # listens on http://127.0.0.1:8080

Endpoints
    GET  /health        liveness probe   -> 200 {"status": "ok", "version": "1.0.20261120183012"}
    GET  /inventory     notes in the ATM -> 200 {"inventory": {"500": 8, ...}, "total_cash": 8000}
    GET  /openapi.json  OpenAPI 3 description of this API (used by the security scan in CI)
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
    ATM_VERSION          build version reported by /health (set by the image build; default "dev")
    ATM_API_KEY          if set (16+ characters), /inventory and /withdrawals need the header
                         "Authorization: Bearer <key>". The health check stays open.

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
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from dispenser import (
    CANNOT_DISPENSE,
    DEFAULT_INVENTORY,
    DEFAULT_MAX_WITHDRAWAL,
    ATMError,
    CashDispenser,
    Notes,
    Validator,
)

MAX_BODY_BYTES = 1024
MIN_API_KEY_LENGTH = 16

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9.+-]{1,64}$")

log = logging.getLogger("atm.service")


# Configuration ----------------------------------------------------------------------------

class ConfigError(ValueError):
    """The service cannot start because an environment variable is wrong."""


@dataclass
class ServiceConfig:
    atm: CashDispenser
    host: str
    port: int
    api_key: str | None
    version: str

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> "ServiceConfig":
        """Read and check the settings from environment variables."""
        port_text = environ.get("ATM_PORT", "8080")
        if not port_text.isdigit() or not 0 <= int(port_text) <= 65535:
            raise ConfigError(f"ATM_PORT must be a number from 0 to 65535, got {port_text!r}")

        try:
            max_withdrawal = Validator.amount(environ.get("ATM_MAX_WITHDRAWAL", str(DEFAULT_MAX_WITHDRAWAL)))
        except ATMError as error:
            raise ConfigError(f"ATM_MAX_WITHDRAWAL is invalid: {error}") from None

        version = environ.get("ATM_VERSION", "dev")
        if not _SAFE_VERSION.match(version):
            raise ConfigError(f"ATM_VERSION must be letters, digits, '.', '+' or '-', got {version[:64]!r}")

        api_key = environ.get("ATM_API_KEY") or None
        if api_key is not None and len(api_key) < MIN_API_KEY_LENGTH:
            raise ConfigError(f"ATM_API_KEY must be at least {MIN_API_KEY_LENGTH} characters long")

        try:
            inventory = Validator.parse_inventory(environ.get("ATM_INVENTORY", DEFAULT_INVENTORY))
            atm = CashDispenser(inventory, max_withdrawal=max_withdrawal)
        except ATMError as error:
            raise ConfigError(f"ATM_INVENTORY is invalid: {error}") from None

        return cls(atm=atm, host=environ.get("ATM_HOST", "127.0.0.1"), port=int(port_text),
                   api_key=api_key, version=version)


# HTTP request handling --------------------------------------------------------------------

class ATMRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests. `self.server` is the ATMServer that owns the ATM."""

    server: "ATMServer"
    server_version = "atm-service"   # do not advertise the Python version
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 10                      # seconds a client may stay silent before we hang up

    # (method, path) -> handler method. Paths in PROTECTED need the API key when one is set.
    ROUTES = {
        ("GET", "/health"): "_get_health",
        ("GET", "/openapi.json"): "_get_openapi",
        ("GET", "/inventory"): "_get_inventory",
        ("POST", "/withdrawals"): "_post_withdrawal",
    }
    PROTECTED = {"/inventory", "/withdrawals"}

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
        self._body_read = False  # the same handler serves every request on a keep-alive connection
        incoming_id = self.headers.get("X-Request-ID", "")
        self._request_id = incoming_id if _SAFE_REQUEST_ID.match(incoming_id) else uuid.uuid4().hex
        path = urlsplit(self.path).path.rstrip("/") or "/"

        try:
            handler_name = self.ROUTES.get((method, path))
            if handler_name is None:
                self._reject_unknown_route(method, path)
            elif path not in self.PROTECTED or self._authorised():
                getattr(self, handler_name)()
        except Exception:  # never show a stack trace to the client
            log.exception("unexpected error", extra={"request_id": self._request_id})
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"}, close=True)

    # Endpoints ------------------------------------------------------------------------------

    def _get_health(self) -> None:
        self._send(HTTPStatus.OK, {"status": "ok", "version": self.server.version})

    def _get_openapi(self) -> None:
        self._send(HTTPStatus.OK, OPENAPI_SPEC)

    def _get_inventory(self) -> None:
        atm = self.server.atm
        self._send(HTTPStatus.OK, {"inventory": _as_json_notes(atm.inventory), "total_cash": atm.total_cash})

    def _post_withdrawal(self) -> None:
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

        try:
            amount = Validator.amount(payload["amount"])
            notes, inventory_after = self.server.atm.dispense_with_inventory(amount)
        except ATMError as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        if notes is None:
            self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {
                "error": CANNOT_DISPENSE,
                "amount": amount,
                "inventory": _as_json_notes(inventory_after),
            })
            return

        self._send(HTTPStatus.OK, {
            "amount": amount,
            "dispensed": _as_json_notes(notes),
            "total_notes": sum(notes.values()),
            "inventory": _as_json_notes(inventory_after),
        })

    # Request checks -------------------------------------------------------------------------

    def _reject_unknown_route(self, method: str, path: str) -> None:
        allowed = sorted(m for (m, p) in self.ROUTES if p == path)
        if allowed:
            self._send(HTTPStatus.METHOD_NOT_ALLOWED,
                       {"error": f"Method {method} not allowed; use {', '.join(allowed)}"},
                       extra_headers={"Allow": ", ".join(allowed)}, close=True)
        else:
            self._send(HTTPStatus.NOT_FOUND, {"error": "Not found"}, close=True)

    def _authorised(self) -> bool:
        expected = self.server.api_key
        if expected is None:
            return True
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() == "bearer" and hmac.compare_digest(token.encode(), expected.encode()):
            return True
        self._send(HTTPStatus.UNAUTHORIZED, {"error": "Missing or invalid API key"},
                   extra_headers={"WWW-Authenticate": "Bearer"}, close=True)
        return False

    def _read_json_body(self) -> dict[str, Any] | None:
        """Return the request body as a dict, or send an error response and return None."""
        raw = self._read_body()
        if raw is None:
            return None

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

    def _read_body(self) -> bytes | None:
        """Read exactly Content-Length bytes, or send an error response and return None."""
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

        try:
            raw = self.rfile.read(length)
        except TimeoutError:
            self._send(HTTPStatus.REQUEST_TIMEOUT, {"error": "Timed out waiting for the request body"}, close=True)
            return None
        self._body_read = True
        if len(raw) < length:
            self._send(HTTPStatus.BAD_REQUEST, {"error": "Request body is shorter than Content-Length"}, close=True)
            return None
        return raw

    # Responses and logging ------------------------------------------------------------------

    def _send(self, status: HTTPStatus, body: dict[str, Any],
              extra_headers: dict[str, str] | None = None, close: bool = False) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Request-ID", getattr(self, "_request_id", "-"))
        for name, value in {**SECURITY_HEADERS, **(extra_headers or {})}.items():
            self.send_header(name, value)
        if close or self._has_unread_body():
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self._log_request(int(status))  # before the body, so the log line is never lost
        if self.command != "HEAD":
            self.wfile.write(data)

    def _has_unread_body(self) -> bool:
        """A body we did not read would be mistaken for the next request, so we must hang up."""
        headers = getattr(self, "headers", None)
        if headers is None or getattr(self, "_body_read", False):
            return False
        return bool(headers.get("Transfer-Encoding")) or headers.get("Content-Length", "0").strip() != "0"

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Malformed requests (bad request line, header too long, ...) also get a JSON answer."""
        if self.request_version == "HTTP/0.9":  # otherwise the status line would be left out
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


def _as_json_notes(notes: Notes) -> dict[str, int]:
    return {str(value): count for value, count in notes.items()}


# Server and start-up ----------------------------------------------------------------------

class ATMServer(ThreadingHTTPServer):
    """An HTTP server that owns one ATM. Each request runs in its own thread; the ATM's lock
    keeps concurrent withdrawals from taking the same notes."""

    daemon_threads = True
    request_queue_size = 128  # the default (5) drops connections when many customers arrive at once

    def __init__(self, atm: CashDispenser, host: str = "127.0.0.1", port: int = 8080,
                 api_key: str | None = None, version: str = "dev"):
        super().__init__((host, port), ATMRequestHandler)
        self.atm = atm
        self.api_key = api_key
        self.version = version


class JsonLogFormatter(logging.Formatter):
    """One JSON object per line: easy to search in any log platform."""

    FIELDS = ("request_id", "method", "path", "status", "duration_ms", "client")

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        entry.update({field: getattr(record, field) for field in self.FIELDS if hasattr(record, field)})
        if record.exc_info:
            entry["error"] = self.formatException(record.exc_info)
        return json.dumps(entry)


class ATMService:
    """Starts the service from environment variables and runs it until SIGTERM or Ctrl+C."""

    def __init__(self, environ: Mapping[str, str] = os.environ):
        self.environ = environ

    def run(self) -> int:
        """Returns the exit code: 0 after a clean shutdown, 2 if the configuration is invalid."""
        self._configure_logging()
        try:
            config = ServiceConfig.from_environment(self.environ)
            server = ATMServer(config.atm, config.host, config.port, config.api_key, config.version)
        except (ConfigError, OSError) as error:
            log.error(f"cannot start: {error}")
            return 2

        if config.api_key is None:
            log.warning("ATM_API_KEY is not set: /inventory and /withdrawals are open to anyone")

        def stop(signum, frame):  # docker stop sends SIGTERM
            log.info("shutting down")
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        log.info(f"atm-dispenser {config.version} listening on http://{config.host}:{server.server_port}")
        try:
            server.serve_forever()
        finally:
            server.server_close()
        return 0

    @staticmethod
    def _configure_logging() -> None:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonLogFormatter())
        logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


# OpenAPI contract -------------------------------------------------------------------------
# Served at GET /openapi.json. It documents the API and lets the OWASP ZAP scan in CI find and
# attack every endpoint.

def _json_response(description: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"description": description, "content": {"application/json": {"schema": schema or {"type": "object"}}}}


_NOTES_SCHEMA = {"type": "object", "additionalProperties": {"type": "integer", "minimum": 0},
                 "example": {"500": 3, "200": 9, "100": 20}}
_ERROR_SCHEMA = {"type": "object", "properties": {"error": {"type": "string"}}}

OPENAPI_SPEC: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "ATM Cash Dispenser API", "version": "1.0.0",
             "description": "Dispenses withdrawals with the fewest notes from a limited inventory."},
    "components": {"securitySchemes": {"apiKey": {"type": "http", "scheme": "bearer"}}},
    "paths": {
        "/health": {"get": {"summary": "Liveness probe and build version", "security": [],
                            "responses": {"200": _json_response("Service is alive")}}},
        "/inventory": {"get": {"summary": "Notes currently in the ATM", "security": [{"apiKey": []}],
                               "responses": {"200": _json_response("Current inventory"),
                                             "401": _json_response("Missing or invalid API key", _ERROR_SCHEMA)}}},
        "/withdrawals": {"post": {
            "summary": "Withdraw an amount",
            "security": [{"apiKey": []}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": {
                "type": "object", "required": ["amount"], "additionalProperties": False,
                "properties": {"amount": {"type": "integer", "minimum": 1, "example": 2700}}}}}},
            "responses": {
                "200": _json_response("Cash dispensed", {"type": "object", "properties": {
                    "amount": {"type": "integer"}, "dispensed": _NOTES_SCHEMA,
                    "total_notes": {"type": "integer"}, "inventory": _NOTES_SCHEMA}}),
                "400": _json_response("Invalid amount or request body", _ERROR_SCHEMA),
                "401": _json_response("Missing or invalid API key", _ERROR_SCHEMA),
                "422": _json_response("Cannot dispense requested amount (inventory unchanged)", _ERROR_SCHEMA),
            },
        }},
        "/openapi.json": {"get": {"summary": "This document", "security": [],
                                  "responses": {"200": _json_response("OpenAPI document")}}},
    },
}


if __name__ == "__main__":
    sys.exit(ATMService().run())
