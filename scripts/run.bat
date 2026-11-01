@echo off
REM another_ap — clone ^& run (windows). nothing global, venv is .\.venv-windows.
cd /d "%~dp0\.."
if exist ".venv-windows\Scripts\python.exe" (
  set PY=.venv-windows\Scripts\python.exe
) else (
  echo $ venv not found - creating .\.venv-windows ^(local only^)…
  py -m venv .venv-windows 2>nul || python -m venv .venv-windows
  set PY=.venv-windows\Scripts\python.exe
)
"%PY%" scripts\run.py %*
