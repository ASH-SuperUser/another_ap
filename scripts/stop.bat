@echo off
REM another_ap stop (windows) — stops a running server via pid file or port scan.
REM usage: scripts\stop.bat [--port P] [--pid-file F]
cd /d "%~dp0\.."
if exist "venv\Scripts\python.exe" (
  set PY=venv\Scripts\python.exe
) else (
  py -3 --version >nul 2>&1 && (set PY=py -3) || (set PY=python)
)
%PY% scripts\stop.py %*
