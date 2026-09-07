#!/usr/bin/env bash
set -euo pipefail

PKG_ROOT="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
BUILD_ROOT="$PKG_ROOT/rbnx-build"
VENV="$BUILD_ROOT/venv"

if ! command -v uv >/dev/null 2>&1; then
  echo "[build] error: uv is required" >&2
  exit 1
fi

uv venv --allow-existing --python "${LINUX_HEALTH_PYTHON:-python3}" "$VENV"
ROBONIX_API="$(rbnx path robonix-api)"
uv pip install --python "$VENV/bin/python" --quiet "$ROBONIX_API[codegen]"
RBNX_CODEGEN_PYTHON="$VENV/bin/python" PATH="$VENV/bin:$PATH" rbnx codegen -p "$PKG_ROOT"

CODEGEN_ROOT="$BUILD_ROOT/codegen"
PYTHONPATH="$ROBONIX_API:$PKG_ROOT:$CODEGEN_ROOT/proto_gen:${PYTHONPATH:-}" \
  "$VENV/bin/python" -c 'from linux_health import collector, driver; collector.collect("/sys")'
echo "[build] done"
