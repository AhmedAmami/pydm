"""The comfortable list view: two-line rows, status pills, hover buttons,
thumbnails, the Add dialog's video preview, and the compact view switch.
Run from the project root: .venv/Scripts/python tests/test_gui_look.py [screenshot-prefix]
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
os.environ["LOCALAPPDATA"] = str(work)  # thumbnail cache goes here
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = sys.argv[1] if len(sys.argv) > 1 else None
sys.path[:0] = [ROOT, os.path.dirname(os.path.abspath(__file__))]

import server  # noqa: E402
from PyQt6.QtCore import QPoint, QStandardPaths, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import gui, streams, theme, ytdl  # noqa: E402
from pydm.engine import Status  # noqa: E402
from pydm.gui import (COL_ACTIONS, COL_NAME, COL_SIZE, COL_STATUS, COMPACT_ONLY, ROW, ActionsDelegate,  # noqa: E402
                      AddDownloadDialog, MainWindow, Thumbnails, friendly_error)
from pydm.streams import Variant  # noqa: E402

failures = []
sys.stdout.reconfigure(errors="replace")


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


app = QApplication([])
app.setApplicationName("PyDM-look-test")
app.setOrganizationName("PyDM-look-test")
app.setStyle("Fusion")
theme.apply(app, "light")


def pump(seconds=0.1):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.02)


def wait(cond, timeout=30):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        pump(0.05)
    return cond()


img = QImage(320, 180, QImage.Format.Format_RGB32)
img.fill(QColor("#7c3aed"))
static = work / "static"
static.mkdir()
img.save(str(static / "thumb.png"))
server.STATIC_ROOT = static
base = server.start()
THUMB = f"{base}/static/thumb.png"
out = work / "out"

from PyQt6.QtCore import QSettings  # noqa: E402

QSettings("PyDM-look-test", "PyDM-look-test").clear()  # before the window reads them (e.g. the saved view)
win = MainWindow()
win.settings.clear()
win.manager.tasks.clear()
win.resize(1180, 700)
win.show()

# --- views ---
check("comfortable view by default: details folded into the rows, buttons column shown",
      win.comfortable and all(win.table.isColumnHidden(c) for c in COMPACT_ONLY)
      and not win.table.isColumnHidden(COL_ACTIONS) and win.table.verticalHeader().defaultSectionSize() == 62)

# --- rows in different states ---
running = win.manager.add(f"{base}/ranges/x?delay=0.2", out, 2, filename="running.iso")
paused = win.manager.add(f"{base}/ranges/x?delay=0.2", out, 2, filename="paused.pdf", start=False)
broken = win.manager.add(f"{base}/missing", out, 2, filename="broken.zip")
done = win.manager.add(f"{base}/ranges/x", out, 4, filename="done.mp3")
wait(lambda: running.status == Status.DOWNLOADING and broken.status == Status.ERROR
     and done.status == Status.COMPLETED, 30)
pump(1.2)
win.refresh()


def row_of(task):
    return win._row_ids.index(task.id)


def data(task):
    return win.table.item(row_of(task), COL_NAME).data(ROW)


d = data(running)
check("downloading: '<done> of <total> · speed · time left' and a bar", " of 12.01 MB" in d["subtitle"]
      and "/s" in d["subtitle"] and "left" in d["subtitle"] and d["bar"] is not None, d["subtitle"])
check("paused: says so, with the percentage", data(paused)["subtitle"].startswith("Paused"), data(paused)["subtitle"])
check("error: one readable line in red", data(broken)["subtitle"] == "Not found on the server (404)"
      and data(broken)["alert"], data(broken)["subtitle"])
check("completed: size · site · date, no bar", "12.01 MB" in data(done)["subtitle"] and "127.0.0.1" in
      data(done)["subtitle"] and data(done)["bar"] is None, data(done)["subtitle"])
check("the old columns still have their values (compact view, tests)",
      win.table.item(row_of(done), COL_SIZE).text() == "12.01 MB"
      and win.table.item(row_of(done), COL_STATUS).text() == "Completed")
check("readable errors", friendly_error("HTTPError: 403 Client Error: Forbidden for url: http://x")
      == "Access denied: the link may have expired (403)" and friendly_error("ConnectionError: timed out")
      == "timed out")

# --- hover buttons ---
keys = lambda t: [a[0] for a in win.row_actions(row_of(t))]  # noqa: E731
check("buttons per state", keys(running) == ["pause", "more"] and keys(paused) == ["resume", "more"]
      and keys(broken) == ["resume", "refresh", "more"] and keys(done) == ["open", "folder", "more"],
      str([keys(t) for t in (running, paused, broken, done)]))


def click_button(task, key):
    row = row_of(task)
    cell = win.table.visualRect(win.table.model().index(row, COL_ACTIONS))
    actions = win.row_actions(row)
    rect = ActionsDelegate.rects(cell, len(actions))[[a[0] for a in actions].index(key)]
    QTest.mouseMove(win.table.viewport(), rect.center().toPoint())
    QTest.mouseClick(win.table.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     rect.center().toPoint())
    pump(0.2)


click_button(running, "pause")
check("the pause button pauses that download", wait(lambda: running.status == Status.PAUSED, 10), running.status)
click_button(paused, "resume")
check("the resume button resumes it", paused.status in (*Status.ACTIVE, Status.QUEUED), paused.status)
opened = []
gui.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toLocalFile()))
click_button(done, "open")
check("the open button opens the file", opened and opened[-1].endswith("done.mp3"), str(opened))
menus = []
win._context_menu = lambda pos: menus.append(pos)
click_button(done, "more")
check("the more button opens the menu for that row", menus and win.selected_tasks() == [done])
win.manager.pause(paused)

# --- thumbnails ---
thumbs = Thumbnails()
first = thumbs.get(THUMB)
check("thumbnail: fetched in the background", first is None and wait(lambda: thumbs.get(THUMB) is not None, 10))
check("…square for the list, kept on disk", thumbs.get(THUMB).width() == Thumbnails.SIZE
      and any(thumbs.folder.glob("*.img")))
server.STATIC_ROOT = None  # offline from here: the disk copy must be enough
check("…and reused from disk without the network", Thumbnails().get(THUMB) is not None)
server.STATIC_ROOT = static

# --- the Add dialog's preview ---
URL = "https://www.youtube.com/watch?v=preview123"
ytdl._meta[URL] = {"title": "Big Buck Bunny", "thumbnail": THUMB, "duration": 635, "channel": "Blender"}
real_lv = streams.list_variants
streams.list_variants = lambda url, headers=None, kind=None: ("ytdl", [Variant("1080", "1080p"), Variant("720", "720p")])
dlg = AddDownloadDialog(win.settings, URL, win, kind="ytdl")
dlg.show()
check("video preview: title and 'site · length · channel'", wait(lambda: dlg.preview.isVisible(), 10)
      and dlg.preview_title.text() == "Big Buck Bunny" and dlg.preview_meta.text() == "YouTube  ·  10:35  ·  Blender",
      dlg.preview_meta.text())
check("…and the thumbnail", wait(lambda: not dlg._thumb_timer.isActive(), 10)
      and dlg.preview_image.pixmap().width() == 160)
if SHOT:
    dlg.grab().save(f"{SHOT}-add.png")
dlg.url.setText("https://example.com/file.zip")
dlg._url_changed()
check("no preview for ordinary files", not dlg.preview.isVisible())
dlg.close()
streams.list_variants = real_lv

if SHOT:
    win.refresh()
    win.grab().save(f"{SHOT}-window.png")

# --- smooth scrolling ---
from PyQt6.QtCore import QPointF  # noqa: E402
from PyQt6.QtGui import QWheelEvent  # noqa: E402

for i in range(30):
    win.manager.add(f"{base}/ranges/x?n={i}", out, 2, filename=f"filler-{i:02d}.bin", start=False)
win.refresh()
pump(0.3)  # let the table lay out the new rows (the scroll range)
bar = win.table.verticalScrollBar()
bar.setValue(0)
row_h = win.table.verticalHeader().defaultSectionSize()


def wheel(angle=0, pixels=0):
    pos = QPointF(200, 100)
    ev = QWheelEvent(pos, win.table.viewport().mapToGlobal(pos), QPoint(0, pixels), QPoint(0, angle),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(win.table.viewport(), ev)


lines = QApplication.wheelScrollLines()
wheel(angle=-120)
seen = []
t0 = time.time()
while time.time() - t0 < 0.5:  # watch the glide
    app.processEvents()
    seen.append(bar.value())
    time.sleep(0.005)
end = round(row_h * lines / 3)
check("one wheel notch glides about one row (not three)", any(0 < v < end for v in seen)
      and abs(bar.value() - end) <= 1, f"passed through {sorted(set(seen))[:6]}... -> {bar.value()}, row {row_h}")
start = bar.value()
wheel(angle=-120)
wheel(angle=-120)
pump(0.5)
check("quick notches add up", abs(bar.value() - start - 2 * round(row_h * lines / 3)) <= 2, f"{start} -> {bar.value()}")
start = bar.value()
wheel(angle=-15, pixels=-25)  # a touchpad: small pixel steps
pump(0.05)
check("touchpads scroll by their own pixels, straight away", bar.value() - start == 25, f"{start} -> {bar.value()}")

# --- compact view, remembered ---
win.act_view.trigger()
check("switching to compact shows the columns and hides the buttons", not win.comfortable
      and not any(win.table.isColumnHidden(c) for c in COMPACT_ONLY) and win.table.isColumnHidden(COL_ACTIONS))
win.manager.shutdown()
win.ipc_server.close()
again = MainWindow()
check("the view choice is remembered", not again.comfortable)
again.manager.shutdown()
again.ipc_server.close()

gui.style_title_bar(win)  # Windows 11 title bar colours: must be harmless anywhere
check("title bar styling is safe to call", True)

print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
