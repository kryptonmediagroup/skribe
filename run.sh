#!/usr/bin/env bash
# Launch Skribe using its dedicated venv.
set -e
VENV="${SKRIBE_VENV:-$HOME/skribe/.venv}"
HERE="$(cd "$(dirname "$0")" && pwd)"

# Ensure piper voice model is available
VOICE_DIR="$HOME/.local/share/piper/voices"
VOICE_FILE="$VOICE_DIR/en_US-lessac-medium.onnx"
if [ ! -f "$VOICE_FILE" ]; then
    mkdir -p "$VOICE_DIR"
    "$VENV/bin/python3" -m piper.download_voices --download-dir "$VOICE_DIR" en_US-lessac-medium
fi

exec "$VENV/bin/python3" -m skribe "$@"
