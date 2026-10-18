#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "$SCRIPT_DIR/../server/.venv/bin/python" ]]; then
    exec "$SCRIPT_DIR/../server/.venv/bin/python" "$SCRIPT_DIR/dev.py" "$@"
fi
exec python3 "$SCRIPT_DIR/dev.py" "$@"
