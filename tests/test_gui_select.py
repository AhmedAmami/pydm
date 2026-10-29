"""Selecting several downloads with the mouse, and deleting them with or without
their files. Run from the project root:
    .venv/Scripts/python tests/test_gui_select.py [screenshot-prefix]
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
from PyQt6.QtCore import QPoint, QStandardPaths, Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import theme  # noqa: E402
from pydm.engine import Status  # noqa: E402
from pydm.gui import COL_CHECK, COL_NAME, DeleteDialog, MainWindow  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


app = QApplication([])
app.setApplicationName("PyDM-select-test")
app.setOrganizationName("PyDM-select-test")
app.setStyle("Fusion")
theme.apply(app, "light")


def pump(seconds=0.2):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.02)


win = MainWindow()
win.settings.clear()
win.manager.tasks.clear()
win.resize(1100, 640)
win.show()
out = Path(tempfile.mkdtemp())
base = server.start()
tasks = [win.manager.add(f"{base}/ranges/x", out, 4, filename=f"file{i}.bin") for i in range(4)]
slow = win.manager.add(f"{base}/ranges/x?delay=0.05", out, 2, filename="unfinished.bin")
t0 = time.time()
while any(t.status != Status.COMPLETED for t in tasks) and time.time() - t0 < 30:
    pump(0.1)
win.manager.pause(slow)
slow.join(5)
win.refresh()
table = win.table


def click(row, col, modifier=Qt.KeyboardModifier.NoModifier):
    rect = table.visualRect(table.model().index(row, col))
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, modifier, rect.center())
    pump(0.05)


def selected_names():
    return sorted(t.filename for t in win.selected_tasks())


click(0, COL_CHECK)
click(2, COL_CHECK)
check("checkboxes add rows to the selection", selected_names() == ["file0.bin", "file2.bin"], str(selected_names()))
click(2, COL_CHECK)
check("clicking a ticked box unticks just that row", selected_names() == ["file0.bin"], str(selected_names()))
click(3, COL_NAME, Qt.KeyboardModifier.ControlModifier)
check("Ctrl+click adds a row", selected_names() == ["file0.bin", "file3.bin"], str(selected_names()))
click(1, COL_NAME)
click(3, COL_NAME, Qt.KeyboardModifier.ShiftModifier)
check("Shift+click selects a range", selected_names() == ["file1.bin", "file2.bin", "file3.bin"],
      str(selected_names()))
start = table.visualRect(table.model().index(0, COL_NAME)).center()
end = table.visualRect(table.model().index(2, COL_NAME)).center()
QTest.mousePress(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
QTest.mouseMove(table.viewport(), end)
QTest.mouseRelease(table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
pump(0.05)
check("dragging across rows selects them", selected_names() == ["file0.bin", "file1.bin", "file2.bin"],
      str(selected_names()))

header = table.horizontalHeader()
QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                 QPoint(header.sectionViewportPosition(COL_CHECK) + 20, header.height() // 2))
pump(0.05)
check("header box selects all", len(win.selected_tasks()) == 5)
win.refresh()
check("status bar counts the selection", win.status_label.text().startswith("5 selected"), win.status_label.text())
if SHOT:
    win.grab().save(f"{SHOT}-window.png")
QTest.mouseClick(header.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                 QPoint(header.sectionViewportPosition(COL_CHECK) + 20, header.height() // 2))
pump(0.05)
check("header box again clears the selection", not win.selected_tasks())

dlg = DeleteDialog([tasks[0], slow], with_files=True)
check("dialog: 'with files' on, unfinished note shown", dlg.with_files.isChecked() and dlg.ok.text() == "Delete with files")
if SHOT:
    dlg.show()
    pump(0.1)
    dlg.grab().save(f"{SHOT}-dialog.png")
dlg.with_files.setChecked(False)
check("dialog button follows the checkbox", dlg.ok.text() == "Delete from list")
only_unfinished = DeleteDialog([slow], True)
check("dialog: 'with files' disabled when nothing is finished", not only_unfinished.with_files.isEnabled())

paths = [t.final_path for t in tasks]
win.delete_tasks([tasks[0], tasks[1]], with_files=False)
check("delete from list keeps the files", paths[0].exists() and paths[1].exists()
      and tasks[0] not in win.manager.tasks and tasks[1] not in win.manager.tasks)
win.delete_tasks([tasks[2], slow], with_files=True)
check("delete with files removes them from disk", not paths[2].exists() and tasks[2] not in win.manager.tasks
      and not any(out.glob("unfinished.bin*")) and slow not in win.manager.tasks, str(list(out.iterdir())))

# A file held open by another program (Windows refuses to delete it).
from PyQt6.QtWidgets import QMessageBox  # noqa: E402
warned = []
QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[2]))
with open(paths[3], "rb"):
    failed = win.delete_tasks([tasks[3]], with_files=True)
check("file in use: reported, kept on disk, still removed from list",
      failed == ["file3.bin"] and paths[3].exists() and tasks[3] not in win.manager.tasks and warned, str(failed))

win.manager.shutdown()
win.ipc_server.close()
print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
