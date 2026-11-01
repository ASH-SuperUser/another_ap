@echo off
REM another_ap — foreground verbose run (windows). blocking, debug logs, ctrl+c stops.
REM usage: scripts\run_fg.bat [--port P] [--host H] [extra run.py args...]
cd /d "%~dp0\.."
if exist ".venv-windows\Scripts\python.exe" (
  set PY=.venv-windows\Scripts\python.exe
) else (
  echo $ venv not found - creating .\.venv-windows ^(local only^)…
  py -m venv .venv-windows 2>nul || python -m venv .venv-windows
  set PY=.venv-windows\Scripts\python.exe
)
"%PY%" scripts\run.py --log-level debug %*
