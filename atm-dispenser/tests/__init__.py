"""Test set-up: make src/ importable, and keep the tests independent of the caller's shell.

ATM_* variables exported in the terminal (for example ATM_API_KEY before run-container.sh)
would otherwise change how the service under test behaves, so they are removed here.
"""

import os
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

for name in [name for name in os.environ if name.startswith("ATM_")]:
    del os.environ[name]
