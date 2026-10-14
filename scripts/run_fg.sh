#!/usr/bin/env bash
# another_ap — foreground verbose run (posix). blocking, debug logs, ctrl+c stops.
# usage: scripts/run_fg.sh [--port P] [--host H] [extra run.py args...]
set -e
cd "$(dirname "$0")/.."
if [ -x "venv/bin/python" ]; then
  PY="venv/bin/python"
elif [ -x "venv/Scripts/python.exe" ]; then
  PY="venv/Scripts/python.exe"   # windows venv reused under WSL interop
else
  echo '$ venv not found — creating ./venv (local only)…'
  python3 -m venv venv
  PY="venv/bin/python"
fi
exec "$PY" scripts/run.py --log-level debug "$@"
