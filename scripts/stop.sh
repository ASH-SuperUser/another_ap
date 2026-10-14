#!/usr/bin/env bash
# another_ap stop (posix) — stops a running server via pid file or port scan.
# usage: scripts/stop.sh [--port P] [--pid-file F]
set -e
cd "$(dirname "$0")/.."
if [ -x "venv/bin/python" ]; then
  PY="venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"   # stop.py is stdlib-only; any python works
else
  PY="python"
fi
exec "$PY" scripts/stop.py "$@"
