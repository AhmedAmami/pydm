"""Logging, and keeping PyDM alive when something unexpected goes wrong.

PyQt ends the program on an unhandled Python exception in a slot (a timer, a
button...) unless sys.excepthook is set. PyDM sets it: the error goes to the log,
the window says once that something went wrong, and the app keeps running.
Hard crashes (in Qt or Python itself) are written to crash.log by faulthandler.

Logs: %LOCALAPPDATA%\\PyDM\\logs (pydm.log, rotated; crash.log).
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import sys
import threading
import traceback
from pathlib import Path

log = logging.getLogger("pydm")
_listeners: list = []   # called with the traceback text of an unexpected error
_crash_file = None


def folder() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "PyDM" / "logs"


def setup() -> None:
    global _crash_file
    try:
        folder().mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(folder() / "pydm.log", maxBytes=1_000_000, backupCount=3,
                                                       encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        _crash_file = open(folder() / "crash.log", "a", encoding="utf-8")  # noqa: SIM115 - open for the process
        faulthandler.enable(_crash_file)
    except OSError:
        pass  # no log is better than no app
    sys.excepthook = _unhandled
    threading.excepthook = lambda args: _unhandled(args.exc_type, args.exc_value, args.exc_traceback,
                                                   thread=args.thread.name if args.thread else "")


def _unhandled(exc_type, exc, tb, thread: str = "") -> None:
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("Unexpected error%s:\n%s", f" in thread {thread}" if thread else "", text)
    if sys.stderr is not None:  # running from a console (the installed app has none)
        try:
            sys.stderr.write(text)
        except (OSError, ValueError):
            pass
    for f in list(_listeners):
        try:
            f(text)
        except Exception:  # noqa: BLE001 - never fail while reporting a failure
            pass


def on_error(callback) -> None:
    _listeners.append(callback)
