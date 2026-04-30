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

# Ensure piper voice model is available
VOICE_DIR="$HOME/.local/share/piper/voices"
VOICE_FILE="$VOICE_DIR/en_US-lessac-medium.onnx"
if [ ! -f "$VOICE_FILE" ]; then
    mkdir -p "$VOICE_DIR"
    "$VENV/bin/python3" -m piper.download_voices --download-dir "$VOICE_DIR" en_US-lessac-medium
fi

exec "$VENV/bin/python3" -m skribe "$@"
