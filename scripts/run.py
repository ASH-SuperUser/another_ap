"""another_ap portable launcher — stdlib only.

Usage (after `git clone`, from repo root or scripts/):
    python scripts/run.py [--host H] [--port P] [--no-install] [--reload]
                          [--log-level debug] [--daemon]

What it does:
  1. finds/creates ./venv (works on Windows + posix, no globals touched)
  2. installs ./requirements.txt into it (unless --no-install)
  3. runs the FastAPI server (uvicorn) in the foreground (blocking),
     or detached in the background with --daemon (writes a pid file
     that scripts/stop.* understands).

Nothing is ever installed globally.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(ROOT, "venv")
REQS = os.path.join(ROOT, "requirements.txt")
DEFAULT_PID_FILE = os.path.join(ROOT, "data", ".another_ap.pid")
DEFAULT_LOG_FILE = os.path.join(ROOT, "data", "another_ap.log")


def venv_python() -> str:
    if os.name == "nt":
        cands = [os.path.join(VENV, "Scripts", "python.exe"),
                 os.path.join(VENV, "Scripts", "pythonw.exe")]
    else:
        cands = [os.path.join(VENV, "bin", "python"),
                 os.path.join(VENV, "bin", "python3")]
        # WSL checkout cloned on Windows: venv may be Windows-style; fall back gracefully
        cands.append(os.path.join(VENV, "Scripts", "python.exe"))
    for c in cands:
        if os.path.isfile(c):
            return c
    return sys.executable


def ensure_venv() -> str:
    py = venv_python()
    if py != sys.executable and os.path.isfile(py):
        return py
    if not os.path.isdir(VENV):
        print("$ venv not found — creating ./venv (local only)…")
        subprocess.check_call([sys.executable, "-m", "venv", VENV])
        py = venv_python()
    return py


def ensure_deps(py: str) -> None:
    # skip install if everything already importable (fast path for repeated runs)
    probe = subprocess.run(
        [py, "-c", "import fastapi, uvicorn, duckdb, jinja2, PIL, mutagen, yt_dlp, httpx"],
        capture_output=True,
    )
    if probe.returncode == 0:
        print("$ deps ok, skipping install")
        return
    print("$ installing requirements into ./venv (local only)…")
    subprocess.check_call([py, "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([py, "-m", "pip", "install", "-r", REQS])


def start_daemon(py: str, cmd: list[str], env: dict, pid_file: str, log_file: str) -> int:
    """Spawn uvicorn detached; write pid file. Returns child pid. Testable hook."""
    os.makedirs(os.path.dirname(pid_file) or ".", exist_ok=True)
    logf = open(log_file, "ab")
    devnull = open(os.devnull, "rb")
    kwargs: dict = {"cwd": ROOT, "env": env, "stdin": devnull,
                    "stdout": logf, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603 - local trusted cmd
    with open(pid_file, "w") as f:
        f.write(str(proc.pid))
    return proc.pid


def main() -> None:
    ap = argparse.ArgumentParser(prog="another_ap")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-install", action="store_true")
    ap.add_argument("--reload", action="store_true")
    ap.add_argument("--log-level", default="info",
                    choices=["debug", "info", "warning", "error"],
                    help="uvicorn log verbosity (foreground shows everything)")
    ap.add_argument("--daemon", action="store_true",
                    help="run detached in background; use scripts/stop.* to stop")
    ap.add_argument("--pid-file", default=DEFAULT_PID_FILE)
    ap.add_argument("--log-file", default=DEFAULT_LOG_FILE)
    args = ap.parse_args()

    py = ensure_venv()
    if not args.no_install:
        ensure_deps(py)

    env = dict(os.environ)
    if args.host:
        env["ANOTHER_AP_HOST"] = args.host
    if args.port:
        env["ANOTHER_AP_PORT"] = str(args.port)

    # resolve host/port for the banner (config defaults apply inside server)
    sys.path.insert(0, ROOT)
    import config as _cfg

    host = args.host or env.get("ANOTHER_AP_HOST", _cfg.HOST)
    port = args.port or int(env.get("ANOTHER_AP_PORT", _cfg.PORT))
    cmd = [py, "-m", "uvicorn", "server.main:app", "--host", host, "--port", str(port),
           "--log-level", args.log_level]
    if args.reload:
        cmd.append("--reload")
    os.chdir(ROOT)
    if args.daemon:
        pid = start_daemon(py, cmd, env, args.pid_file, args.log_file)
        print(f"$ another_ap backgrounded at http://{host}:{port}  (pid {pid})")
        print(f"$ logs  -> {args.log_file}")
        print(f"$ stop  -> scripts/stop.*  (pid file {args.pid_file})")
        return
    print(f"$ another_ap serving at http://{host}:{port}  (ctrl+c to stop, blocking)")
    os.execvpe(py, cmd, env)


if __name__ == "__main__":
    main()
