#!/usr/bin/env bash
set -euo pipefail

PKG_ROOT="${RBNX_PACKAGE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
CODEGEN_ROOT="$PKG_ROOT/rbnx-build/codegen"
PYTHON="$PKG_ROOT/rbnx-build/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "linux_health is not built; run rbnx build first" >&2
  exit 1
fi

export PYTHONPATH="$(rbnx path robonix-api):$PKG_ROOT:$CODEGEN_ROOT/proto_gen:${PYTHONPATH:-}"
exec "$PYTHON" -m linux_health.driver
