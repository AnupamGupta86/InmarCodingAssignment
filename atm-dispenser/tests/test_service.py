"""Tests for service.py: a real server on a free port, real HTTP requests.

Covers the functional API, every error path, security behaviour (limits, headers, API key,
no stack traces), concurrent withdrawals over HTTP, fuzzing, and the end-to-end smoke-test script.
"""

import contextlib
import http.client
import io
import json
import logging
import os
import random
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import service
from dispenser import CashDispenser
from service import (
    MAX_BODY_BYTES,
    SECURITY_HEADERS,
    ATMRequestHandler,
    ATMServer,
    ATMService,
    ConfigError,
    JsonLogFormatter,
    ServiceConfig,
)
from tests.test_dispenser import SEED, random_value

sys.path.insert(0, str(Path(__file__).resolve().parent / "e2e"))
import smoke_test  # noqa: E402

API_KEY = "test-key-0123456789abcdef"


class RunningServer:
    """Start the service in a background thread on a free port; stop it afterwards."""

    def __init__(self, inventory=None, api_key=None, max_withdrawal=200_000):
        self.atm = CashDispenser(inventory or {500: 8, 200: 10, 100: 20}, max_withdrawal=max_withdrawal)
        self.server = ATMServer(self.atm, "127.0.0.1", 0, api_key)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, body=None, headers=None, raw_body=None):
        """Send one request. Returns (status, json body or None, response headers)."""
        headers = dict(headers or {})
        data = raw_body
        if body is not None:
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            text = response.read().decode()
            parsed = json.loads(text) if text else None
            return response.status, parsed, {k.lower(): v for k, v in response.getheaders()}
        finally:
            connection.close()

    def withdraw(self, amount, headers=None):
        return self.request("POST", "/withdrawals", {"amount": amount}, headers)


# ------------------------------------------------------------------------- functional tests

class HealthAndInventoryTests(unittest.TestCase):
    def test_health(self):
        with RunningServer() as srv:
            self.assertEqual(srv.request("GET", "/health")[:2], (200, {"status": "ok", "version": "dev"}))

    def test_inventory(self):
        with RunningServer() as srv:
            status, body, _ = srv.request("GET", "/inventory")
            self.assertEqual(status, 200)
            self.assertEqual(body, {"inventory": {"500": 8, "200": 10, "100": 20}, "total_cash": 8000})

    def test_openapi_document_lists_every_endpoint(self):
        with RunningServer() as srv:
            status, body, _ = srv.request("GET", "/openapi.json")
            self.assertEqual(status, 200)
            self.assertEqual(body["openapi"], "3.0.3")
            self.assertEqual(set(body["paths"]),
                             {"/health", "/inventory", "/withdrawals", "/openapi.json"})

    def test_trailing_slash_and_query_string_are_ignored(self):
        with RunningServer() as srv:
            self.assertEqual(srv.request("GET", "/health/")[0], 200)
            self.assertEqual(srv.request("GET", "/inventory?x=1")[0], 200)


class WithdrawalApiTests(unittest.TestCase):
    def test_example_from_the_brief(self):
        with RunningServer() as srv:
            status, body, _ = srv.withdraw(2700)
            self.assertEqual(status, 200)
            self.assertEqual(body, {
                "amount": 2700,
                "dispensed": {"500": 5, "200": 1},
                "total_notes": 6,
                "inventory": {"500": 3, "200": 9, "100": 20},
            })
            self.assertEqual(srv.request("GET", "/inventory")[1]["inventory"], {"500": 3, "200": 9, "100": 20})

    def test_greedy_trap(self):
        with RunningServer({4: 1, 3: 2}) as srv:
            self.assertEqual(srv.withdraw(6)[1]["dispensed"], {"3": 2})

    def test_amount_as_text_is_accepted(self):
        with RunningServer() as srv:
            self.assertEqual(srv.withdraw("2700")[0], 200)

    def test_cannot_dispense_returns_422_and_keeps_inventory(self):
        with RunningServer() as srv:
            status, body, _ = srv.withdraw(250)
            self.assertEqual(status, 422)
            self.assertEqual(body["error"], "Cannot dispense requested amount")
            self.assertEqual(body["inventory"], {"500": 8, "200": 10, "100": 20})

    def test_withdrawals_until_the_atm_is_empty(self):
        with RunningServer({500: 2}) as srv:
            self.assertEqual(srv.withdraw(500)[0], 200)
            self.assertEqual(srv.withdraw(500)[0], 200)
            self.assertEqual(srv.withdraw(500)[0], 422)
            self.assertEqual(srv.request("GET", "/inventory")[1]["total_cash"], 0)


class InvalidInputApiTests(unittest.TestCase):
    """Each validation rule from the brief, through the API: 400 with a clear message."""

    def assert_bad_request(self, srv, payload, message_part):
        status, body, _ = srv.request("POST", "/withdrawals", payload)
        self.assertEqual(status, 400, body)
        self.assertIn(message_part, body["error"])

    def test_invalid_amounts(self):
        cases = [
            (0, "greater than zero"),
            (-5, "cannot be negative"),
            ("abc", "must be a number"),
            (12.5, "whole number"),
            (True, "must be a number"),
            (None, "must be a number"),
            ([2700], "must be a number"),
            ({"value": 1}, "must be a number"),
            ("9" * 30, "too large"),
            (10 ** 300, "exceeds the limit"),
            (200_001, "exceeds the limit"),
        ]
        with RunningServer() as srv:
            for amount, message in cases:
                with self.subTest(amount=amount):
                    self.assert_bad_request(srv, {"amount": amount}, message)
            self.assertEqual(srv.atm.inventory, {500: 8, 200: 10, 100: 20})

    def test_missing_and_unknown_fields(self):
        with RunningServer() as srv:
            self.assert_bad_request(srv, {}, 'Missing field "amount"')
            self.assert_bad_request(srv, {"amount": 100, "account": "x"}, "Unknown field(s): account")

    def test_body_must_be_a_json_object(self):
        with RunningServer() as srv:
            for raw in (b"[2700]", b"2700", b'"2700"', b"null", b"[" * 500 + b"]" * 500):
                with self.subTest(raw=raw[:20]):
                    status, body, _ = srv.request("POST", "/withdrawals", raw_body=raw,
                                                  headers={"Content-Type": "application/json"})
                    self.assertEqual(status, 400)
                    self.assertIn("JSON object", body["error"])

    def test_malformed_json(self):
        with RunningServer() as srv:
            for raw in (b"{amount: 1}", b"", b"{\"amount\": 1", b"\xff\xfe", b"nan"):
                with self.subTest(raw=raw[:20]):
                    status, body, _ = srv.request("POST", "/withdrawals", raw_body=raw,
                                                  headers={"Content-Type": "application/json"})
                    self.assertEqual(status, 400)


# ------------------------------------------------------------------------- HTTP protocol tests

class HttpProtocolTests(unittest.TestCase):
    def test_unknown_path_is_404(self):
        with RunningServer() as srv:
            for path in ("/", "/admin", "/withdrawals/1", "/../etc/passwd", "/%2e%2e/etc/passwd"):
                with self.subTest(path=path):
                    self.assertEqual(srv.request("GET", path)[0], 404)

    def test_wrong_method_is_405_with_allow_header(self):
        with RunningServer() as srv:
            status, _, headers = srv.request("GET", "/withdrawals")
            self.assertEqual((status, headers["allow"]), (405, "POST"))
            for method in ("PUT", "PATCH", "DELETE"):
                with self.subTest(method=method):
                    self.assertEqual(srv.request(method, "/withdrawals")[0], 405)
            status, _, headers = srv.request("POST", "/inventory", {"amount": 1})
            self.assertEqual((status, headers["allow"]), (405, "GET"))

    def test_unsupported_method_is_501(self):
        with RunningServer() as srv:
            self.assertEqual(srv.request("OPTIONS", "/health")[0], 501)

    def test_content_type_must_be_json(self):
        with RunningServer() as srv:
            status, _, _ = srv.request("POST", "/withdrawals", raw_body=b'{"amount": 100}',
                                       headers={"Content-Type": "text/plain"})
            self.assertEqual(status, 415)
            status, _, _ = srv.request("POST", "/withdrawals", raw_body=b'{"amount": 100}',
                                       headers={"Content-Type": "application/json; charset=utf-8"})
            self.assertEqual(status, 200)

    def test_body_too_large_is_413(self):
        with RunningServer() as srv:
            big = b'{"amount": 100, "x": "' + b"a" * MAX_BODY_BYTES + b'"}'
            status, _, _ = srv.request("POST", "/withdrawals", raw_body=big,
                                       headers={"Content-Type": "application/json"})
            self.assertEqual(status, 413)

    def test_missing_or_bad_content_length(self):
        with RunningServer() as srv:
            self.assertEqual(self._raw(srv, b"POST /withdrawals HTTP/1.1\r\nHost: x\r\n\r\n")[:12], b"HTTP/1.1 411")
            self.assertEqual(
                self._raw(srv, b"POST /withdrawals HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n")[:12],
                b"HTTP/1.1 400")
            chunked = b"POST /withdrawals HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"
            self.assertEqual(self._raw(srv, chunked)[:12], b"HTTP/1.1 411")

    def test_garbage_request_gets_a_json_400(self):
        with RunningServer() as srv:
            reply = self._raw(srv, b"GARBAGE\r\n\r\n")
            self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:40])
            self.assertIn(b'"error"', reply)

    def test_keep_alive_connection_serves_several_requests(self):
        with RunningServer() as srv:
            connection = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
            for _ in range(3):
                connection.request("GET", "/health")
                self.assertEqual(json.loads(connection.getresponse().read())["status"], "ok")
            connection.close()

    def test_body_on_a_get_request_cannot_smuggle_a_second_request(self):
        """A GET carrying a body that is itself a complete withdrawal request: the server must
        not read that body as a new request (that would dispense money nobody asked for)."""
        hidden = (b"POST /withdrawals HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Content-Length: 15\r\n\r\n{\"amount\": 500}")
        request = b"GET /health HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n" % len(hidden) + hidden
        with RunningServer() as srv:
            reply = self._raw(srv, request)
            self.assertEqual(reply.count(b"HTTP/1.1 "), 1, reply)
            self.assertIn(b"Connection: close", reply)
            self.assertEqual(srv.atm.inventory, {500: 8, 200: 10, 100: 20})

    def test_slow_body_gets_408(self):
        with mock.patch.object(ATMRequestHandler, "timeout", 1), RunningServer() as srv:
            reply = self._raw(srv, b"POST /withdrawals HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                                   b"Content-Length: 50\r\n\r\n{\"amount\": 500}")
            self.assertTrue(reply.startswith(b"HTTP/1.1 408"), reply[:60])
            self.assertEqual(srv.atm.inventory, {500: 8, 200: 10, 100: 20})

    def test_body_cut_short_gets_400(self):
        with RunningServer() as srv, socket.create_connection(("127.0.0.1", srv.port), timeout=10) as sock:
            sock.sendall(b"POST /withdrawals HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                         b"Content-Length: 50\r\n\r\n{\"amount\": 500}")
            sock.shutdown(socket.SHUT_WR)  # the client stops sending
            reply = b"".join(iter(lambda: sock.recv(4096), b""))
        self.assertTrue(reply.startswith(b"HTTP/1.1 400"), reply[:60])
        self.assertIn(b"shorter than Content-Length", reply)

    @staticmethod
    def _raw(srv, data):
        with socket.create_connection(("127.0.0.1", srv.port), timeout=10) as sock:
            sock.sendall(data)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)  # the server closes the connection after an error
            return b"".join(chunks)


# ------------------------------------------------------------------------- security tests

class SecurityTests(unittest.TestCase):
    def test_security_headers_on_every_response(self):
        with RunningServer() as srv:
            for method, path, body in (("GET", "/health", None), ("GET", "/nope", None),
                                       ("POST", "/withdrawals", {"amount": -1})):
                _, _, headers = srv.request(method, path, body)
                for name, value in SECURITY_HEADERS.items():
                    with self.subTest(path=path, header=name):
                        self.assertEqual(headers[name.lower()], value)
                self.assertTrue(headers["content-type"].startswith("application/json"))

    def test_server_does_not_reveal_python_version(self):
        with RunningServer() as srv:
            self.assertEqual(srv.request("GET", "/health")[2]["server"], "atm-service")

    def test_request_id_is_echoed_only_when_safe(self):
        with RunningServer() as srv:
            headers = srv.request("GET", "/health", headers={"X-Request-ID": "abc-123"})[2]
            self.assertEqual(headers["x-request-id"], "abc-123")
            headers = srv.request("GET", "/health", headers={"X-Request-ID": "bad id<script>"})[2]
            self.assertRegex(headers["x-request-id"], r"^[0-9a-f]{32}$")

    def test_unexpected_errors_do_not_leak_details(self):
        with RunningServer() as srv:
            with mock.patch.object(srv.atm, "dispense_with_inventory", side_effect=RuntimeError("secret detail")), \
                    self.assertLogs("atm.service", level="ERROR"):
                status, body, _ = srv.withdraw(100)
            self.assertEqual((status, body), (500, {"error": "Internal server error"}))

    def test_api_key_protects_inventory_and_withdrawals(self):
        with RunningServer(api_key=API_KEY) as srv:
            good = {"Authorization": f"Bearer {API_KEY}"}
            for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": API_KEY},
                            {"Authorization": f"Basic {API_KEY}"}):
                with self.subTest(headers=headers):
                    status, _, response_headers = srv.withdraw(100, headers)
                    self.assertEqual(status, 401)
                    self.assertEqual(response_headers["www-authenticate"], "Bearer")
                    self.assertEqual(srv.request("GET", "/inventory", headers=headers)[0], 401)
            self.assertEqual(srv.atm.inventory, {500: 8, 200: 10, 100: 20})  # nothing dispensed
            self.assertEqual(srv.withdraw(100, good)[0], 200)
            self.assertEqual(srv.request("GET", "/inventory", headers=good)[0], 200)
            self.assertEqual(srv.request("GET", "/health")[0], 200)  # probes stay open

    def test_logs_are_json_and_do_not_contain_request_bodies(self):
        with RunningServer() as srv, self.assertLogs("atm.service", level="INFO") as captured:
            srv.request("POST", "/withdrawals", {"amount": "secret-looking-value"})
        formatted = [JsonLogFormatter().format(record) for record in captured.records]
        for line in formatted:
            entry = json.loads(line)
            self.assertIn("status", entry)
        self.assertFalse(any("secret-looking-value" in line for line in formatted))


# ------------------------------------------------------------------------- concurrency over HTTP

class ConcurrentHttpTests(unittest.TestCase):
    def test_each_response_reports_the_inventory_right_after_its_own_withdrawal(self):
        with RunningServer({500: 30}) as srv, ThreadPoolExecutor(max_workers=30) as pool:
            bodies = list(pool.map(lambda _: srv.withdraw(500)[1], range(30)))
        notes_left = sorted(body["inventory"]["500"] for body in bodies)
        self.assertEqual(notes_left, list(range(30)))  # 29, 28, ... 0: each value exactly once

    def test_parallel_requests_never_share_notes(self):
        with RunningServer({500: 8}) as srv:
            with ThreadPoolExecutor(max_workers=20) as pool:
                statuses = list(pool.map(lambda _: srv.withdraw(500)[0], range(25)))
            self.assertEqual(statuses.count(200), 8)
            self.assertEqual(statuses.count(422), 17)
            self.assertEqual(srv.atm.inventory, {500: 0})


# ------------------------------------------------------------------------- configuration

class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = ServiceConfig.from_environment({})
        self.assertEqual((config.host, config.port, config.api_key), ("127.0.0.1", 8080, None))
        self.assertEqual(config.version, "dev")
        self.assertEqual(config.atm.inventory, {500: 8, 200: 10, 100: 20})
        self.assertEqual(config.atm.max_withdrawal, 200_000)

    def test_version_is_reported_by_health(self):
        atm = CashDispenser({500: 1})
        server = ATMServer(atm, "127.0.0.1", 0, version="1.0.20261120183012")
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
            connection.request("GET", "/health")
            self.assertEqual(json.loads(connection.getresponse().read())["version"], "1.0.20261120183012")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_custom_values(self):
        config = ServiceConfig.from_environment({"ATM_INVENTORY": "4:1, 3:2", "ATM_HOST": "0.0.0.0", "ATM_PORT": "9000",
                              "ATM_MAX_WITHDRAWAL": "5000", "ATM_API_KEY": API_KEY,
                              "ATM_VERSION": "1.0.20261120183012"})
        self.assertEqual(config.version, "1.0.20261120183012")
        self.assertEqual(config.atm.inventory, {4: 1, 3: 2})
        self.assertEqual((config.host, config.port, config.api_key), ("0.0.0.0", 9000, API_KEY))

    def test_state_file_keeps_the_inventory_between_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            environ = {"ATM_STATE_FILE": str(Path(folder) / "atm.json")}
            ServiceConfig.from_environment(environ).atm.dispense(2700)
            self.assertEqual(ServiceConfig.from_environment(environ).atm.inventory, {500: 3, 200: 9, 100: 20})

    def test_a_bad_state_file_stops_the_service_from_starting(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "atm.json"
            bad.write_text("not json", encoding="utf-8")
            for path in (str(bad), str(Path(folder) / "missing" / "atm.json")):
                with self.subTest(path=path), self.assertRaisesRegex(ConfigError, "ATM_STATE_FILE"):
                    ServiceConfig.from_environment({"ATM_STATE_FILE": path})

    def test_invalid_values_stop_the_service_from_starting(self):
        for env, message in (({"ATM_PORT": "abc"}, "ATM_PORT"), ({"ATM_PORT": "70000"}, "ATM_PORT"),
                             ({"ATM_MAX_WITHDRAWAL": "0"}, "ATM_MAX_WITHDRAWAL"),
                             ({"ATM_API_KEY": "short"}, "ATM_API_KEY"),
                             ({"ATM_VERSION": "1.0 <script>"}, "ATM_VERSION"),
                             ({"ATM_INVENTORY": "{}"}, "ATM_INVENTORY"),
                             ({"ATM_INVENTORY": "500:-1"}, "ATM_INVENTORY")):
            with self.subTest(env=env), self.assertRaisesRegex(ConfigError, message):
                ServiceConfig.from_environment(env)

    def test_main_starts_and_stops_cleanly(self):
        handlers = {}
        environ = {"ATM_PORT": "0", "ATM_INVENTORY": "4:1, 3:2"}
        result = []
        with mock.patch.object(service.signal, "signal", lambda sig, handler: handlers.setdefault(sig, handler)), \
                self.assertLogs("atm.service", level="INFO") as captured:
            thread = threading.Thread(target=lambda: result.append(ATMService(environ).run()))
            thread.start()
            deadline = time.time() + 10
            while not any("listening on" in r.getMessage() for r in captured.records) and time.time() < deadline:
                time.sleep(0.05)
            handlers[signal.SIGTERM](signal.SIGTERM, None)  # what "docker stop" does
            thread.join(timeout=10)
        logging.getLogger().handlers.clear()
        self.assertEqual(result, [0])
        messages = [r.getMessage() for r in captured.records]
        self.assertTrue(any("ATM_API_KEY is not set" in m for m in messages))
        self.assertIn("shutting down", messages)

    def test_server_start_does_not_look_up_its_own_name_in_dns(self):
        with mock.patch("socket.getfqdn", side_effect=AssertionError("reverse DNS lookup")):
            server = ATMServer(CashDispenser({500: 1}), "127.0.0.1", 0)
        server.server_close()
        self.assertEqual(server.server_name, "127.0.0.1")

    @unittest.skipIf(sys.platform == "win32", "SIGTERM cannot be sent to a process on Windows")
    def test_real_process_stops_on_sigterm(self):
        src = Path(__file__).resolve().parent.parent / "src"
        process = subprocess.Popen(
            [sys.executable, str(src / "service.py")], cwd=src.parent.parent, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,  # a crash message ends up in `lines`
            env={**os.environ, "ATM_PORT": "0", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        watchdog = threading.Timer(30, process.kill)  # never hang the test run, whatever happens
        watchdog.start()
        try:
            lines = []
            while not any("listening on" in line for line in lines):
                line = process.stdout.readline()
                if not line:  # the process ended without starting
                    break
                lines.append(line)
            self.assertTrue(any("listening on" in line for line in lines), lines)
            process.send_signal(signal.SIGTERM)
            self.assertEqual(process.wait(timeout=10), 0)
        finally:
            watchdog.cancel()
            process.kill()
            process.stdout.close()

    def test_log_formatter_includes_the_error_for_exceptions(self):
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord("atm.service", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
        entry = json.loads(JsonLogFormatter().format(record))
        self.assertEqual((entry["level"], entry["message"]), ("ERROR", "failed"))
        self.assertIn("ValueError: boom", entry["error"])

    def test_main_exits_with_code_2_on_bad_config(self):
        with self.assertLogs("atm.service", level="ERROR"):
            self.assertEqual(ATMService({"ATM_PORT": "abc"}).run(), 2)
        logging.getLogger().handlers.clear()


# ================================================================================================
# Fuzz: the service never answers 500, whatever bytes it receives
# ================================================================================================

class HttpFuzzTests(unittest.TestCase):
    def test_service_never_returns_500(self):
        rng = random.Random(SEED)
        with RunningServer() as srv:
            for _ in range(300):
                kind = rng.randrange(3)
                if kind == 0:
                    raw = json.dumps({"amount": random_value(rng)}, default=str).encode()
                elif kind == 1:
                    raw = bytes(rng.randrange(256) for _ in range(rng.randint(0, 60)))
                else:
                    raw = json.dumps({random_value(rng).__repr__(): random_value(rng)}, default=str).encode()
                status, body, _ = srv.request("POST", "/withdrawals", raw_body=raw,
                                              headers={"Content-Type": "application/json"})
                self.assertIn(status, (200, 400, 422), (raw, body))
                self.assertIn("error" if status != 200 else "dispensed", body)


# ================================================================================================
# The end-to-end smoke-test script, run against a real server, so it can be trusted
# ================================================================================================

def run_smoke_test(url, api_key):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = smoke_test.main(url, api_key=api_key)
    return code, output.getvalue()


class SmokeScriptTests(unittest.TestCase):
    def test_passes_against_a_healthy_service(self):
        with RunningServer() as srv:
            code, output = run_smoke_test(f"http://127.0.0.1:{srv.port}", api_key="")
        self.assertEqual(code, 0, output)
        self.assertIn("All checks passed", output)

    def test_passes_with_the_right_api_key(self):
        with RunningServer(api_key=API_KEY) as srv:
            code, output = run_smoke_test(f"http://127.0.0.1:{srv.port}", api_key=API_KEY)
        self.assertEqual(code, 0, output)
        self.assertIn("request without API key returns 401", output)

    def test_fails_clearly_without_the_api_key(self):
        with RunningServer(api_key=API_KEY) as srv:
            code, output = run_smoke_test(f"http://127.0.0.1:{srv.port}", api_key="")
        self.assertEqual(code, 1)
        self.assertIn("set ATM_API_KEY", output)

    def test_checks_the_expected_version(self):
        with RunningServer() as srv:  # the test server reports version "dev"
            with mock.patch.dict(os.environ, {"EXPECTED_VERSION": "dev"}):
                self.assertEqual(run_smoke_test(f"http://127.0.0.1:{srv.port}", api_key="")[0], 0)
            with mock.patch.dict(os.environ, {"EXPECTED_VERSION": "1.0.20261120183012"}):
                code, output = run_smoke_test(f"http://127.0.0.1:{srv.port}", api_key="")
        self.assertEqual(code, 1)
        self.assertIn("FAIL  service reports version 1.0.20261120183012", output)

    def test_works_on_an_empty_atm(self):
        with RunningServer({500: 0}) as srv:
            code, output = run_smoke_test(f"http://127.0.0.1:{srv.port}", api_key="")
        self.assertEqual(code, 0, output)


if __name__ == "__main__":
    unittest.main()
