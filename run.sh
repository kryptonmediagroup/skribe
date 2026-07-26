#!/usr/bin/env bash
# Launch Skribe using its dedicated venv.
set -e
VENV="${SKRIBE_VENV:-$HOME/skribe/.venv}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Create venv on first run.
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment at $VENV..."
    python3 -m venv "$VENV"
fi

# Always (re)install requirements. pip is idempotent — already-satisfied
# packages print a single line and skip download/install, so this is
# fast on subsequent runs but picks up additions and upgrades to the
# pinned versions in requirements.txt.
echo "Ensuring Skribe dependencies are installed..."
"$VENV/bin/python3" -m pip install --upgrade pip >/dev/null
"$VENV/bin/python3" -m pip install -r "$HERE/requirements.txt"

# KittenTTS downloads its voice model from Hugging Face on first use, so no
# voice bootstrap step is needed here.

exec "$VENV/bin/python3" -m skribe "$@"
