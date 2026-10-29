"""Local IPC between PyDM processes (GUI, native messaging host, `python -m pydm URL`).

The GUI listens on a QLocalServer (a named pipe on Windows). A client writes one
JSON line and waits for "ok". This also makes the GUI single-instance.
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtNetwork import QLocalSocket

SERVER_NAME = os.environ.get("PYDM_IPC_NAME") or f"pydm-{getpass.getuser()}"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def send(message: dict, timeout_ms: int = 2000) -> bool:
    """Deliver a message to a running GUI. Returns False if none is listening."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    try:
        sock.write(json.dumps(message).encode() + b"\n")
        if not sock.waitForBytesWritten(timeout_ms):
            return False
        reply = b""
        deadline = time.monotonic() + timeout_ms / 1000
        while not reply.endswith(b"\n") and time.monotonic() < deadline:
            if sock.waitForReadyRead(100):
                reply += bytes(sock.readAll())
        return reply.strip() == b"ok"
    finally:
        sock.disconnectFromServer()


def launch_gui() -> None:
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):  # installed: pydm-host.exe sits next to PyDM.exe
        cmd = [str(exe.with_name("PyDM.exe"))]
    else:
        pythonw = exe.with_name("pythonw.exe")
        cmd = [str(pythonw if pythonw.exists() else exe), "-m", "pydm"]
    if os.name != "nt":
        subprocess.Popen(cmd, cwd=PROJECT_ROOT, start_new_session=True)
        return
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # The browser runs native hosts in a job object; break away so closing
        # the host doesn't take the GUI down with it.
        subprocess.Popen(cmd, cwd=PROJECT_ROOT, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        subprocess.Popen(cmd, cwd=PROJECT_ROOT, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def send_or_launch(message: dict, wait_s: float = 15) -> bool:
    """Send to the running GUI, starting it first if necessary."""
    if send(message):
        return True
    launch_gui()
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        time.sleep(0.3)
        if send(message):
            return True
    return False
