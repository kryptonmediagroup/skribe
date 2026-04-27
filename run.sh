#!/usr/bin/env bash
# Launch Skribe using its dedicated venv.
set -e
VENV="${SKRIBE_VENV:-$HOME/skribe/.venv}"
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$VENV/bin/python3" -m skribe "$@"
