@echo off
REM another_ap — clone ^& run (windows). nothing global, venv is .\venv.
cd /d "%~dp0\.."
if exist "venv\Scripts\python.exe" (
  set PY=venv\Scripts\python.exe
) else (
  echo $ venv not found - creating .\venv ^(local only^)…
  py -m venv venv 2>nul || python -m venv venv
  set PY=venv\Scripts\python.exe
)
"%PY%" scripts\run.py %*
