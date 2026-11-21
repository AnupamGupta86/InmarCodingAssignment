#!/usr/bin/env bash
#
# Print the build version: <major>.<minor>.<IST date and time>, for example 1.0.20261120183012.
# IST is India Standard Time (UTC+05:30).
# Major and minor come from atm-dispenser/pyproject.toml, so there is one place to change them.

set -euo pipefail
cd "$(dirname "$0")/.."

major_minor="$(sed -n 's/^version = "\([0-9]*\.[0-9]*\)\..*"$/\1/p' atm-dispenser/pyproject.toml)"

if [[ -z "${major_minor}" ]]; then
  echo "Could not read major.minor from atm-dispenser/pyproject.toml" >&2
  exit 1
fi

if [[ "$(TZ=Asia/Kolkata date +%z)" != "+0530" ]]; then
  echo "The time zone Asia/Kolkata is not available on this machine" >&2   # date would silently use UTC
  exit 1
fi

echo "${major_minor}.$(TZ=Asia/Kolkata date +%Y%m%d%H%M%S)"
