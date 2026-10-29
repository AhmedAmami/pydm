"""Offscreen GUI smoke test. Run from the project root:
    .venv/Scripts/python tests/test_gui.py [screenshot.png]
"""
import os
import sys
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOT = sys.argv[1] if len(sys.argv) > 1 else None
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server as srvmod  # noqa: E402

sys.path.insert(0, PROJECT)
from PyQt6.QtCore import QStandardPaths, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)  # don't touch the real AppData
from pydm.gui import MainWindow, APP_NAME  # noqa: E402
from pydm.engine import Status  # noqa: E402

app = QApplication([])
app.setApplicationName(APP_NAME + "-test")
app.setOrganizationName(APP_NAME + "-test")
app.setStyle("Fusion")
win = MainWindow()
win.settings.clear()
win.manager.tasks.clear()
win.show()

tmp = tempfile.mkdtemp()
base = srvmod.start()
done = win.manager.add(f"{base}/ranges/x", tmp, 8, filename="finished.iso")
slow = win.manager.add(f"{base}/ranges/x?slow0=0.05&delay=0.03", tmp, 6, filename="ubuntu-24.04-desktop.iso")
paused = win.manager.add(f"{base}/ranges/x?delay=0.1", tmp, 4, filename="paused-video.mp4")
err = win.manager.add(f"{base}/missing", tmp, 4, filename="broken-link.zip")

QTimer.singleShot(1500, lambda: win.manager.pause(paused))
QTimer.singleShot(3000, lambda: win.table.selectRow(1))


def finish():
    win.refresh()
    if SCREENSHOT:
        win.grab().save(SCREENSHOT)
    print("statuses:", [(t.filename, t.status) for t in win.manager.tasks])
    print("rows:", win.table.rowCount(), "status bar:", win.status_label.text())
    print("details:", win.lbl_info.text())
    ok = (done.status == Status.COMPLETED and paused.status == Status.PAUSED
          and err.status == Status.ERROR and win.table.rowCount() == 4)
    win.close()
    print("GUI SMOKE:", "PASS" if ok else "FAIL")
    app.exit(0 if ok else 1)


QTimer.singleShot(4500, finish)
sys.exit(app.exec())
