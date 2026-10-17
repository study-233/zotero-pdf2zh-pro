#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
VENV="$ROOT/.tmp/website-venv"
export UV_CACHE_DIR="$ROOT/.tmp/cache/uv-website"
unset VIRTUAL_ENV

if ! command -v uv >/dev/null 2>&1; then
    printf 'website: 缺少 uv，请先安装 uv 后重试。\n' >&2
    exit 1
fi

if [[ ! -x "$VENV/bin/python" ]]; then
    uv venv --python "$(command -v python3)" "$VENV"
fi

uv pip install --python "$VENV/bin/python" --requirement "$ROOT/website/requirements.txt"
exec "$VENV/bin/python" "$SCRIPT_DIR/build_site.py" "$@"
