"""another_ap stop — stdlib only, cross-platform.

Usage:
    python scripts/stop.py [--port P] [--pid-file F]

How it finds the server:
  1. pid file written by `scripts/run.py --daemon` (default data/.another_ap.pid)
  2. fallback: scan processes for `server.main:app` listening on the port
     (covers servers backgrounded manually with `run.sh &`, etc.)

Kills gracefully first (SIGTERM / taskkill), then forcefully.
Never kills itself. Exits 0 if something was stopped, 1 if nothing found.
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PID_FILE = os.path.join(ROOT, "data", ".another_ap.pid")
MARKER = "server.main:app"


def resolve_port(cli_port: int | None) -> int:
    if cli_port:
        return cli_port
    env = os.environ.get("ANOTHER_AP_PORT")
    if env:
        return int(env)
    sys.path.insert(0, ROOT)
    import config as _cfg  # local, stdlib-only

    return int(_cfg.PORT)


def read_pidfile(path: str) -> int | None:
    try:
        with open(path) as f:
            return int(f.read().strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def posix_cmdline(pid: int) -> list[str] | None:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [p.decode(errors="replace") for p in f.read().split(b"\0") if p]
    except OSError:
        return None


def posix_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError):
        return False
    # a zombie (unreaped child) still answers kill(pid, 0) — treat as dead
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[-1].split()[0]
        return state != "Z"
    except (OSError, IndexError):
        return True  # non-/proc systems: fall back to kill() answer


def is_our_server(tokens: list[str], port: int) -> bool:
    blob = " ".join(tokens)
    return MARKER in blob and str(port) in tokens


def posix_scan(port: int) -> list[int]:
    found: list[int] = []
    me = os.getpid()
    if os.path.isdir("/proc"):
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            if pid == me:
                continue
            toks = posix_cmdline(pid)
            if toks and is_our_server(toks, port) and posix_alive(pid):
                found.append(pid)
        return found
    # macOS / BSD fallback: parse ps output
    try:
        out = subprocess.run(["ps", "-ax", "-o", "pid=,command="],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return found
    for line in out.stdout.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)", line)
        if not m:
            continue
        pid = int(m.group(1))
        if pid == me:
            continue
        if is_our_server(m.group(2).split(), port) and posix_alive(pid):
            found.append(pid)
    return found


def posix_terminate(pid: int, sig: int = signal.SIGTERM, wait: float = 5.0) -> bool:
    try:
        os.kill(pid, sig)
    except OSError:
        return not posix_alive(pid)
    deadline = time.time() + wait
    while time.time() < deadline:
        if not posix_alive(pid):
            return True
        time.sleep(0.2)
    if sig != signal.SIGKILL:
        return posix_terminate(pid, signal.SIGKILL, 3.0)
    return not posix_alive(pid)


def win_listen_pids(port: int) -> list[int]:
    """Map listening TCP port -> pids via netstat (Windows)."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return []
    pids: list[int] = []
    for line in out.stdout.splitlines():
        parts = line.split()
        # TCP    127.0.0.1:5050    0.0.0.0:0    LISTENING    1234
        if len(parts) == 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            if re.search(rf":{port}$", parts[1]) and parts[4].isdigit():
                pids.append(int(parts[4]))
    return pids


def win_image(pid: int) -> str:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip().splitlines()[0] if out.stdout.strip() else "?"
    except (OSError, subprocess.SubprocessError, IndexError):
        return "?"


def win_terminate(pid: int, wait: float = 5.0) -> bool:
    subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, timeout=10)
    deadline = time.time() + wait
    while time.time() < deadline:
        if "INFO: No tasks" in win_image(pid) or win_image(pid) == "?":
            # tasklist prints INFO line when pid is gone; "?" also means gone-ish
            if not win_listen_pids_membership(pid):
                return True
        time.sleep(0.3)
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
    time.sleep(0.5)
    return not win_listen_pids_membership(pid)


def win_listen_pids_membership(pid: int) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=10)
        return bool(out.stdout.strip()) and "No tasks" not in out.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser(prog="another_ap-stop")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--pid-file", default=DEFAULT_PID_FILE)
    args = ap.parse_args()
    port = resolve_port(args.port)
    stopped: list[int] = []

    # 1) pid file (written by run.py --daemon)
    pid = read_pidfile(args.pid_file)
    if pid is not None:
        if pid == os.getpid():
            print("$ refusing to kill stop.py itself; removing stale pid file")
        elif os.name == "nt":
            img = win_image(pid)
            if "No tasks" in img or img == "?":
                print(f"$ stale pid file (pid {pid} gone), removing")
            else:
                print(f"$ pid file -> pid {pid} ({img.split(',')[0]}) …")
                if win_terminate(pid):
                    stopped.append(pid)
        else:
            if not posix_alive(pid):
                print(f"$ stale pid file (pid {pid} gone), removing")
            else:
                toks = posix_cmdline(pid) or []
                if toks and not is_our_server(toks, port):
                    print(f"$ pid {pid} is not another_ap ({' '.join(toks)[:100]}), leaving it alone")
                else:
                    print(f"$ pid file -> stopping pid {pid} …")
                    if posix_terminate(pid):
                        stopped.append(pid)
        try:
            if pid in stopped or (os.name != "nt" and not posix_alive(pid)):
                os.remove(args.pid_file)
        except OSError:
            pass

    # 2) fallback scan by port + marker
    if os.name == "nt":
        for p in win_listen_pids(port):
            if p == os.getpid() or p in stopped:
                continue
            print(f"$ port {port} listener -> stopping pid {p} ({win_image(p).split(chr(44))[0]}) …")
            if win_terminate(p):
                stopped.append(p)
    else:
        for p in posix_scan(port):
            if p in stopped:
                continue
            print(f"$ scan -> stopping pid {p} …")
            if posix_terminate(p):
                stopped.append(p)

    if stopped:
        print(f"$ stopped another_ap pid(s): {', '.join(map(str, stopped))}")
        return 0
    print(f"$ no running another_ap server found (port {port})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
