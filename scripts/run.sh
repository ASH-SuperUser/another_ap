#!/usr/bin/env bash
# another_ap — clone && run (posix). nothing global, venv is ./.venv-linux.
set -e
cd "$(dirname "$0")/.."
if [ -x ".venv-linux/bin/python" ]; then
  PY=".venv-linux/bin/python"
elif [ -x ".venv-windows/Scripts/python.exe" ]; then
  PY=".venv-windows/Scripts/python.exe"   # windows venv reused under WSL interop
else
  echo '$ venv not found — creating ./.venv-linux (local only)…'
  python3 -m venv .venv-linux
  PY=".venv-linux/bin/python"
fi
exec "$PY" scripts/run.py "$@"
