"""Queues and the scheduler in the window. The power action (sleep/shut down) is
replaced by a recorder, so nothing actually happens to the computer.
Run from the project root: .venv/Scripts/python tests/test_gui_queues.py [screenshot-prefix]
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
from PyQt6.QtCore import QStandardPaths, QTime  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import theme  # noqa: E402
from pydm.engine import Status  # noqa: E402
from pydm.gui import COL_STATUS, AddDownloadDialog, MainWindow, SchedulerDialog  # noqa: E402
from pydm.queues import MAIN_QUEUE  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


app = QApplication([])
app.setApplicationName("PyDM-queues-test")
app.setOrganizationName("PyDM-queues-test")
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
win = MainWindow()
win.settings.clear()
win.resize(1180, 700)
win.show()
powered = []
win.power = powered.append
out = Path(tempfile.mkdtemp())
base = server.start()

# Add dialog offers the queues
dlg = AddDownloadDialog(win.settings, f"{base}/ranges/x", win, queues=[(q.id, q.name) for q in win.queues.queues])
options = [dlg.when.itemText(i) for i in range(dlg.when.count())]
check("Add dialog: Start now / Add paused / Add to Main queue", options == ["Start now", "Add paused",
                                                                             "Add to Main queue"], str(options))
dlg.when.setCurrentIndex(2)
check("…choosing the queue adds it there, paused", dlg.queue_id() == MAIN_QUEUE and not dlg.start_now())

tasks = []
for i in range(3):
    t = win.manager.add(f"{base}/ranges/x?delay=0.01&n={i}", out, 2, filename=f"night{i}.bin", start=False)
    win.queues.add_tasks(MAIN_QUEUE, [t.id])
    tasks.append(t)
win.refresh()

sidebar = [win.sidebar.item(i).text() for i in range(win.sidebar.count()) if not win.sidebar.item(i).isHidden()]
check("sidebar has a QUEUES section with the main queue", "QUEUES" in sidebar and "Main queue   3" in sidebar,
      str(sidebar))
row = next(i for i in range(win.sidebar.count()) if win.sidebar.item(i).text().startswith("Main queue"))
win.sidebar.setCurrentRow(row)
win.refresh()
check("selecting it shows the queue bar and its downloads", win.queue_bar.isVisible()
      and win.queue_start.isVisible() and len(win._row_ids) == 3, win.queue_info.text())
check("waiting downloads say 'In queue'", win.table.item(0, COL_STATUS).text() == "In queue")

win.table.selectRow(2)
win.move_in_queue(-2)
check("Move up reorders the queue and the list", win._row_ids[0] == tasks[2].id
      and win.queues.get(MAIN_QUEUE).task_ids[0] == tasks[2].id)
if SHOT:
    win.table.clearSelection()
    win.refresh()
    win.grab().save(f"{SHOT}-queue.png")

# Run it, then the "when done" countdown
win.queues.get(MAIN_QUEUE).after = "shutdown"
win.queue_start.click()
pump(0.3)
active = [t.filename for t in tasks if t.status in Status.ACTIVE]
check("Start queue runs one download (the top one)", active == ["night2.bin"], str(active))
check("…and the bar switches to 'Stop queue'", win.queue_stop.isVisible() and not win.queue_start.isVisible())
check("finishes everything", wait(lambda: all(t.status == Status.COMPLETED for t in tasks), 90))
check("then shows the shutdown countdown", wait(lambda: getattr(win, "_countdown", None) is not None
                                                and win._countdown.isVisible(), 5)
      and "shut down in 60 seconds" in win._countdown.text(), getattr(win, "_countdown", None)
      and win._countdown.text())
if SHOT:
    win.grab().save(f"{SHOT}-countdown.png")
pump(2.2)
check("…counting down", "in 58 seconds" in win._countdown.text() or "in 57 seconds" in win._countdown.text(),
      win._countdown.text())
check("…and nothing happens before the end", powered == [])
cancel = next(b for b in win._countdown.buttons() if "Shut down" not in b.text())
cancel.click()
pump(1.5)
check("Cancel stops it", powered == [] and not win._countdown.isVisible())

# Same again, but "Shut down now"
for t in tasks:
    win.manager.remove(t, True)
t = win.manager.add(f"{base}/ranges/x?n=again", out, 2, filename="again.bin", start=False)
win.queues.add_tasks(MAIN_QUEUE, [t.id])
win.queues.start(MAIN_QUEUE)
wait(lambda: t.status == Status.COMPLETED, 60)
wait(lambda: win._countdown.isVisible(), 5)
now = next(b for b in win._countdown.buttons() if "Shut down" in b.text())
now.click()
pump(0.2)
check("'Shut down now' runs the power action", powered == ["shutdown"], str(powered))

# Scheduler dialog edits the queue
sd = SchedulerDialog(win, MAIN_QUEUE)
sd.show()
sd.start_on.setChecked(True)
sd.start_time.setTime(QTime(1, 30))
for i, b in enumerate(sd.day_boxes):
    b.setChecked(i < 5)
sd.stop_on.setChecked(True)
sd.stop_time.setTime(QTime(6, 0))
sd.after.setCurrentIndex(sd.after.findData("sleep"))
sd.max_active.setValue(2)
q = win.queues.get(MAIN_QUEUE)
check("scheduler dialog edits the queue", q.start_enabled and q.start_time == "01:30" and q.days == [0, 1, 2, 3, 4]
      and q.stop_enabled and q.stop_time == "06:00" and q.after == "sleep" and q.max_active == 2,
      q.schedule_text())
check("…and shows when it runs next", sd.next_label.text().startswith("Next start:"), sd.next_label.text())
sd._new()
sd.name.setText("Weekend")
sd.name.textEdited.emit("Weekend")
new_q = sd.queue()
check("new queue from the dialog", new_q.name == "Weekend" and len(win.queues.queues) == 2)
if SHOT:
    sd.grab().save(f"{SHOT}-scheduler.png")
sd.accept()
win._sync_queue_items()
win.refresh()
names = [win.sidebar.item(i).text() for i in range(win.sidebar.count())]
check("new queue appears in the sidebar", any(n.startswith("Weekend") for n in names), str(names))

# Right-click "Add to queue" and "Remove from queue"
win.sidebar.setCurrentRow(0)
win.refresh()
win.table.selectRow(0)
win.add_selected_to_queue(new_q.id)
check("Add to queue (context menu)", win.queues.queue_of(t.id) is new_q)
win.table.selectRow(0)
win.remove_selected_from_queue()
check("Remove from queue", win.queues.queue_of(t.id) is None)
win.table.selectRow(0)
win.delete_tasks(win.selected_tasks(), False)
check("deleting a download also takes it out of queues", all(t.id not in q.task_ids for q in win.queues.queues))

win.manager.shutdown()
win.ipc_server.close()
print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
