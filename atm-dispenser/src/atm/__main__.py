"""Allows `python -m atm ...` (see atm/cli.py for the options)."""

import sys

from atm.cli import main

if __name__ == "__main__":
    sys.exit(main())
