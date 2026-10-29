"""Connections changed while downloading, the completion window, the Microsoft
Defender scan (a real scan of a clean file; a threat is simulated, so no test
virus is ever written to disk) and queues continuing after a restart.
Run from the project root: .venv/Scripts/python tests/test_gui_extras.py [screenshot-prefix]
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
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = sys.argv[1] if len(sys.argv) > 1 else None
sys.path[:0] = [ROOT, os.path.dirname(os.path.abspath(__file__))]

import server  # noqa: E402
from PyQt6.QtCore import QStandardPaths  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import scan, theme  # noqa: E402
from pydm.engine import Status  # noqa: E402
from pydm.gui import COL_STATUS, MainWindow  # noqa: E402
from pydm.queues import MAIN_QUEUE  # noqa: E402

failures = []
sys.stdout.reconfigure(errors="replace")  # ✓ and ⚠ in messages; the console may be cp1252


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


app = QApplication([])
app.setApplicationName("PyDM-extras-test")
app.setOrganizationName("PyDM-extras-test")
app.setStyle("Fusion")
theme.apply(app, "light")


def pump(seconds=0.1):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.02)


def wait(cond, timeout=60):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        pump(0.05)
    return cond()


data = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
for f in ("downloads.json", "queues.json"):
    (data / f).unlink(missing_ok=True)
base = server.start()
out = Path(tempfile.mkdtemp())
win = MainWindow()
win.settings.clear()
win.resize(1180, 700)
win.show()


def select(task):
    win.refresh()
    win.table.clearSelection()
    win.table.selectRow(win._row_ids.index(task.id))
    win.refresh()


# --- defender result parsing (no scanning) ---
sample = ("Scan starting...\nScan finished.\nScanning C:\\x.exe found 1 threats.\n\n"
          "----------------------------- Threat information ------------------------------\n"
          "Threat                  : Virus:DOS/EICAR_Test_File\nResources               : 1 total\n")
check("Defender output: threat name read", scan.parse(2, sample).status == "threat"
      and scan.parse(2, sample).detail == "Virus:DOS/EICAR_Test_File")
check("Defender output: clean / error", scan.parse(0, "").status == "clean" and scan.parse(5, "boom").status == "error")
check("which files get scanned", scan.should_scan("a.exe", "risky") and scan.should_scan("a.ZIP", "risky")
      and not scan.should_scan("a.mp4", "risky") and scan.should_scan("a.mp4", "all")
      and not scan.should_scan("a.exe", "off"))

# --- connections while downloading ---
slow = win.manager.add(f"{base}/ranges/x?delay=0.4", out, 2, filename="slow.bin")
wait(lambda: slow.status == Status.DOWNLOADING, 20)
select(slow)
check("details show a Connections control with the current value", win.conn_spin.isVisible()
      and win.conn_spin.value() == 2)
win.conn_spin.setValue(6)
check("raising it adds connections to the running download",
      wait(lambda: slow.snapshot()["active_connections"] == 6, 10), str(slow.snapshot()["active_connections"]))
win.conn_spin.setValue(1)
check("lowering it retires connections", wait(lambda: slow.snapshot()["active_connections"] == 1, 10),
      str(slow.snapshot()["active_connections"]))
win.manager.pause(slow)
slow.join(10)

# --- completion window + real Defender scan of a clean file ---
win.settings.setValue("virus_scan", "all")
if scan.scanner():
    t1 = win.manager.add(f"{base}/ranges/x?n=1", out, 4, filename="setup-one.exe")
    check("a finished download opens the completion window", wait(lambda: win._done_window is not None
                                                                   and win._done_window.isVisible(), 30))
    done = win._done_window
    check("…naming the file", "setup-one.exe" in done.name.toolTip(), done.name.text())
    check("…and it's scanned by Microsoft Defender: no threats", wait(
        lambda: "no threats" in done.scan_line.text(), 120), done.scan_line.text())
    check("…Open becomes available after the scan", done.open_btn.isEnabled())
    if SHOT:
        done.grab().save(f"{SHOT}-done.png")
    select(t1)
    check("the scan result is in the details", "no threats" in win.lbl_info.text(), win.lbl_info.text())

    t2 = win.manager.add(f"{base}/ranges/x?n=2", out, 4, filename="second.bin")
    wait(lambda: "second.bin" in done.name.toolTip(), 30)
    check("another download finishing updates the same window", "1 more download" in done.more.text()
          and done.isVisible(), done.more.text())
else:
    print("[SKIP] Microsoft Defender not available: real scan not tested")

# --- a threat (simulated scanner result) ---
real_scan = scan.scan
scan.scan = lambda path, timeout=600: scan.ScanResult("threat", "Trojan:Win32/Fake.Test")
scan.scanner.cache_clear()
scanner_available = scan.scanner
scan.scanner = lambda: "fake-mpcmdrun.exe"
bad = win.manager.add(f"{base}/ranges/x?n=3", out, 4, filename="cracked-game.exe")
wait(lambda: win._scans.get(bad.id) not in (None, "scanning"), 30)
pump(0.5)
select(bad)
check("threat: status column says so", win.table.item(win._row_ids.index(bad.id), COL_STATUS).text()
      == "Threat found")
check("threat: the completion window shows it in red",
      win._done_window.task_id == bad.id and win._done_window.title.text() == "Threat found"
      and "Trojan:Win32/Fake.Test" in win._done_window.scan_line.text())
check("threat: Open is disabled", not win._done_window.open_btn.isEnabled())
if SHOT:
    win._done_window.grab().save(f"{SHOT}-threat.png")
scan.scan, scan.scanner = real_scan, scanner_available

# The window can be turned off
win._done_window.hide()
win.settings.setValue("done_window", False)
quiet = win.manager.add(f"{base}/ranges/x?n=4", out, 4, filename="quiet.bin")
wait(lambda: quiet.status == Status.COMPLETED, 30)
pump(0.5)
check("'don't show this window' is respected", not win._done_window.isVisible())

# --- queues continue after a restart ---
q1 = win.manager.add(f"{base}/ranges/x?delay=0.3&n=5", out, 2, filename="night-a.bin", start=False)
q2 = win.manager.add(f"{base}/ranges/x?delay=0.3&n=6", out, 2, filename="night-b.bin", start=False)
win.queues.add_tasks(MAIN_QUEUE, [q1.id, q2.id])
win.queues.start(MAIN_QUEUE)
wait(lambda: q1.status == Status.DOWNLOADING, 20)
win.close()  # (no tray offscreen, so this really closes, like quitting)
check("closing pauses the queue's download", q1.status == Status.PAUSED)
win2 = MainWindow()
pump(0.5)
again = win2.queues.get(MAIN_QUEUE)
r1 = win2.manager.get(q1.id)
check("on the next start the queue continues where it was", again.running
      and r1.status in (*Status.ACTIVE, Status.QUEUED), f"{again.running} {r1.status}")
win2.settings.setValue("resume_queues", False)
win2.close()
win3 = MainWindow()
pump(0.5)
check("…unless that's turned off in Settings", not win3.queues.get(MAIN_QUEUE).running)
win3.manager.shutdown()
win3.ipc_server.close()

print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
