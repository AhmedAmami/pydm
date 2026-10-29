"""PyDM must not shut down on an unexpected error: it's logged and the app keeps
running. Also the automatic yt-dlp check that used to crash the installed app 20
seconds after start (QStandardPaths.isTestModeEnabled doesn't exist in PyQt6).
Run from the project root: .venv/Scripts/python tests/test_resilience.py
"""
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["PYDM_IPC_NAME"] = f"pydm-test-{uuid.uuid4().hex[:8]}"
work = Path(tempfile.mkdtemp())
os.environ["LOCALAPPDATA"] = str(work)            # logs go here, not to the real folder
os.environ["PYDM_UPDATES_DIR"] = str(work / "updates")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT]

from PyQt6.QtCore import QStandardPaths, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import gui, log, updates  # noqa: E402

failures = []
sys.stdout.reconfigure(errors="replace")


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


log.setup()
app = QApplication([])
app.setApplicationName("PyDM-resilience-test")
app.setOrganizationName("PyDM-resilience-test")
win = gui.MainWindow()
win.settings.clear()


def pump(seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.02)


# An error in a timer (what used to end the program with 0xc0000409)
QTimer.singleShot(0, lambda: 1 / 0)
pump(0.8)
text = (log.folder() / "pydm.log").read_text(encoding="utf-8")
check("an unexpected error is logged with its traceback", "ZeroDivisionError" in text and "Traceback" in text)
check("…the window says so once and keeps running", "kept running" in win.statusBar().currentMessage()
      and win.isEnabled(), win.statusBar().currentMessage())
QTimer.singleShot(0, lambda: [][1])
pump(0.5)
check("a second error doesn't nag again", win._error_reported and "IndexError" in
      (log.folder() / "pydm.log").read_text(encoding="utf-8"))

# The automatic yt-dlp check, as the installed app runs it 20 s after start
check("test mode is detected (tests don't contact PyPI by themselves)", gui.in_test_mode())
win.check_ytdlp_update(auto=True)
check("…so the automatic check is skipped quietly", not win._update_busy)
gui.in_test_mode = lambda: False   # now exactly like the real app
updates.check = lambda: {"current": "2026.10.26", "latest": "2026.10.26", "newer": False, "files": []}
win.check_ytdlp_update(auto=True)
t0 = time.time()
while (win._update_busy or win._update_results) and time.time() - t0 < 10:
    win.refresh()
    pump(0.1)
check("the automatic check runs without error, like in the installed app",
      win._update_last == ("current", "2026.10.26"), str(win._update_last))
check("…and is logged", "yt-dlp update check: current" in (log.folder() / "pydm.log").read_text(encoding="utf-8"))

gui.force_foreground(win)  # must never raise, even off-screen
check("bringing a window to the front is safe to call", True)

win.manager.shutdown()
win.ipc_server.close()
print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
