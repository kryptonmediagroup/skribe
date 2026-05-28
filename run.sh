#!/usr/bin/env bash
# Launch Skribe using its dedicated venv.
set -e
VENV="${SKRIBE_VENV:-$HOME/skribe/.venv}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Create venv and install dependencies on first run
if [ ! -d "$VENV" ]; then
    echo "Creating virtual environment at $VENV..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip
    "$VENV/bin/pip" install -r "$HERE/requirements.txt"
    echo "Virtual environment ready."
fi

# KittenTTS downloads its voice model from Hugging Face on first use, so no
# voice bootstrap step is needed here.

exec "$VENV/bin/python3" -m skribe "$@"
