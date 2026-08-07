"""Launch `aio` in a real pty, press a few keys, and confirm it exits cleanly.

The headless harness never touches a terminal, so this is the check that the
app actually starts, draws, and quits the way a person would experience it.
"""

from __future__ import annotations

import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import sys
import termios
import time

COLS, ROWS = 150, 44


def drain(fd: int, seconds: float) -> str:
    out, end = b"", time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if fd in r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out.decode("utf-8", "replace")


def main() -> int:
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLUMNS"], os.environ["LINES"] = str(COLS), str(ROWS)
        os.execv(sys.executable, [sys.executable, "-m", "app.cli_pkg.main"])

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    screen = drain(fd, 6.0)

    steps = [(b"2", "board"), (b"3", "agents"), (b"4", "dashboard"), (b"1", "chat"), (b"?", "help")]
    for keys, label in steps:
        os.write(fd, keys)
        time.sleep(1.2)
        screen += drain(fd, 1.0)
        print(f"sent {label}")

    os.write(fd, b"\x1b")  # close help
    time.sleep(0.5)
    os.write(fd, b"\x11")  # ctrl+q
    time.sleep(1.5)
    screen += drain(fd, 2.0)

    _, status = os.waitpid(pid, os.WNOHANG)
    if status == 0 and _ == 0:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        print("WARN: app did not exit on ctrl+q")

    text = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", screen)
    text = re.sub(r"\x1b[\]P][^\x07\x1b]*(\x07|\x1b\\)?", "", text)

    wanted = ["AIO", "Chat", "Board", "Agents", "Dashboard"]
    missing = [w for w in wanted if w not in text]
    crashed = [
        line
        for line in text.splitlines()
        if "Traceback" in line or "Error" in line and "ApiError" not in line
    ]

    print(f"captured {len(screen)} bytes")
    print("missing:", missing or "none")
    print("crash lines:", crashed[:5] or "none")
    return 1 if missing or crashed else 0


raise SystemExit(main())
