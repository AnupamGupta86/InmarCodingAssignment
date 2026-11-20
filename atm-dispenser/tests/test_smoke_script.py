"""The end-to-end smoke test script, run against a real server, so we know it can be trusted."""

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.test_service import API_KEY, RunningServer

sys.path.insert(0, str(Path(__file__).resolve().parent / "e2e"))
import smoke_test  # noqa: E402


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
