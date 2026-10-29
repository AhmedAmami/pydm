"""Refreshing an expired link: the download continues from where it stopped with
the new link, through the Refresh link dialog, the browser, the clipboard or a
pasted link. Also HLS videos (same quality kept, or a clean restart).
Run from the project root: .venv/Scripts/python tests/test_refresh.py
"""
import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["PYDM_IPC_NAME"] = f"pydm-test-{uuid.uuid4().hex[:8]}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.dirname(os.path.abspath(__file__))]

import server  # noqa: E402
import test_streams  # noqa: E402
from PyQt6.QtCore import QStandardPaths  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import gui  # noqa: E402
from pydm.engine import Status  # noqa: E402
from pydm.gui import MainWindow  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


app = QApplication([])
app.setApplicationName("PyDM-refresh-test")
app.setOrganizationName("PyDM-refresh-test")


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


media = Path(tempfile.mkdtemp())
test_streams.make_media(media)
shutil.copytree(media / "low", media / "mirror" / "low")    # the same video on "another server"
shutil.copytree(media / "high", media / "mirror" / "high")
shutil.copy(media / "master.m3u8", media / "mirror" / "master.m3u8")
server.STATIC_ROOT = media
base = server.start()
out = Path(tempfile.mkdtemp())

win = MainWindow()
win.settings.clear()
win.manager.tasks.clear()
win.show()
answers = []
QMessageBox.question = staticmethod(lambda *a, **k: answers.pop(0) if answers else QMessageBox.StandardButton.No)


def expire_midway(url, name):
    task = win.manager.add(url, out, 4, filename=name)
    wait(lambda: task.snapshot()["downloaded"] > 2_000_000, 30)
    win.manager.pause(task)
    task.join(10)
    return task


# 1. A file whose link expires
task = expire_midway(f"{base}/ranges/x?token=a&delay=0.1", "movie.bin")
kept = task.snapshot()["downloaded"]
server.EXPIRED.add("a")
win.manager.resume(task)
wait(lambda: task.status == Status.ERROR, 20)
win.refresh()
row = win._row_ids.index(task.id)
win.table.selectRow(row)
win.refresh()
check("expired link: error with a hint and a Refresh link button", "Refresh link" in win.lbl_error.text()
      and win.btn_refresh.isVisible() and win.act_refresh.isEnabled(), win.lbl_error.text())

dlg = win.refresh_link()
check("Refresh link dialog opens, waiting", dlg.isVisible() and "Waiting" in dlg.status.text())
tasks_before = len(win.manager.tasks)
n0 = len(server.stats["ranges"])
# The browser hands over the re-started download (new token, cookies, referrer):
win._incoming.append({"type": "download", "url": f"{base}/ranges/x?token=b&delay=0.01", "cookies": "sid=new",
                      "referrer": "https://files.example/page", "userAgent": "TestUA"})
win._process_incoming()
check("the browser's new link goes to the waiting download, not a new one",
      len(win.manager.tasks) == tasks_before and "token=b" in task.url and task.headers.get("Cookie") == "sid=new"
      and not dlg.isVisible() and win._refresh_target is None)
wait(lambda: task.status == Status.COMPLETED, 60)
first = [r for r in server.stats["ranges"][n0:] if r != (0, 0)][:4]  # (0, 0) = size checks
check("continued from where it stopped (kept the data)", all(start > 0 for start, _ in first) and kept > 2_000_000,
      str(first))
check("the finished file is exactly right", task.final_path.read_bytes() == server.DATA)

# 2. A new link to a different file: asked; "No" keeps the dialog open
task2 = expire_midway(f"{base}/ranges/x?token=c&delay=0.1", "other.bin")
dlg = win.refresh_link(task2)
answers.append(QMessageBox.StandardButton.No)
ok = dlg.use(f"{base}/ranges/x?token=d&trim=100")
check("different file size: asks first; No = not used", not ok and dlg.isVisible() and "different file"
      in dlg.status.text() and "token=c" in task2.url, dlg.status.text())
# 3. A link that doesn't work either
server.EXPIRED.add("dead")
ok = dlg.use(f"{base}/ranges/x?token=dead")
check("a new link that doesn't work is reported", not ok and "doesn't work" in dlg.status.text(), dlg.status.text())
# 4. Copying a link (no file extension) while the dialog is open
gui.QApplication.activeWindow = staticmethod(lambda: None)
QGuiApplication.clipboard().setText(f"{base}/ranges/x?token=e")
pump(0.5)
check("a copied link (any link) is used by the open dialog", "token=e" in task2.url and not dlg.isVisible())
check("…and the download finishes", wait(lambda: task2.status == Status.COMPLETED, 60)
      and task2.final_path.read_bytes() == server.DATA)

# 5. HLS: the same video from another server keeps its segments; another video starts over
server.STATIC_DELAY = 0.15
from pydm.streams import list_variants  # noqa: E402
_, variants = list_variants(f"{base}/static/master.m3u8")
low = next(v for v in variants if v.label.startswith("180p"))
video = win.manager.add(f"{base}/static/master.m3u8", out, 2, "clip", kind="hls", variant=low.id)
wait(lambda: video.snapshot()["segments"] and sum(s[2] for s in video.snapshot()["segments"]) >= 3, 30)
win.manager.pause(video)
video.join(10)
done_before = sum(s[2] for s in video.snapshot()["segments"])
server.STATIC_DELAY = 0
ok, _ = win.apply_new_link(video, f"{base}/static/mirror/master.m3u8", {})
done_after = sum(s[2] for s in video.snapshot()["segments"])
check("HLS from another server: same quality, finished segments kept", ok and "mirror" in video.variant
      and video.quality.startswith("180p") and done_after >= done_before >= 3, f"{done_before} -> {done_after}")
check("…and completes at that quality", wait(lambda: video.status == Status.COMPLETED, 60)
      and video.quality.startswith("180p"), f"{video.status} {video.error}")

server.STATIC_DELAY = 0.15
video2 = win.manager.add(f"{base}/static/master.m3u8", out, 2, "clip2", kind="hls", variant=low.id)
wait(lambda: video2.snapshot()["segments"] and sum(s[2] for s in video2.snapshot()["segments"]) >= 2, 30)
win.manager.pause(video2)
video2.join(10)
server.STATIC_DELAY = 0
ok, _ = win.apply_new_link(video2, f"{base}/static/aes/index.m3u8", {})
check("a link to a different video: starts over cleanly instead of mixing segments", ok
      and wait(lambda: video2.status == Status.COMPLETED, 60), f"{video2.status} {video2.error}")

win.manager.shutdown()
win.ipc_server.close()
print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
