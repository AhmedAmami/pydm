"""GUI test for video streams: the Add dialog detects a playlist, loads its
qualities in the background, and the chosen quality is what gets downloaded.
Run from the project root: .venv/Scripts/python tests/test_gui_streams.py [screenshot-prefix]
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["PYDM_IPC_NAME"] = f"pydm-test-{uuid.uuid4().hex[:8]}"
SHOT = sys.argv[1] if len(sys.argv) > 1 else None
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server  # noqa: E402
import test_streams  # noqa: E402,F401  (reuses make_media; importing doesn't run the tests)
from PyQt6.QtCore import QStandardPaths  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm.engine import Status  # noqa: E402
from pydm.gui import COL_SIZE, AddDownloadDialog, MainWindow  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def wait_until(app, cond, timeout=30.0):
    import time
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    return cond()


media, out = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
app = QApplication([])
app.setApplicationName("PyDM-stream-gui-test")
app.setStyle("Fusion")
try:
    test_streams.make_media(media)
    server.STATIC_ROOT = media
    base = server.start() + "/static"
    win = MainWindow()
    win.manager.tasks.clear()
    win.settings.setValue("download_dir", str(out))
    win.show()

    dlg = AddDownloadDialog(win.settings, f"{base}/master.m3u8", win, filename="My Video")
    dlg.show()
    check("dialog spots an HLS link", dlg.kind == "hls" and dlg.form.isRowVisible(dlg.quality))
    loaded = wait_until(app, lambda: dlg.quality.count() == 2 and dlg.quality.isEnabled())
    labels = [dlg.quality.itemText(i) for i in range(dlg.quality.count())]
    check("qualities load in the background", loaded and labels[0].startswith("360p"), str(labels))
    dlg.quality.setCurrentIndex(1)
    check("chosen quality maps to its playlist", dlg.variant() == f"{base}/low/index.m3u8", str(dlg.variant()))
    if SHOT:
        dlg.grab().save(f"{SHOT}-dialog.png")

    plain = AddDownloadDialog(win.settings, "https://example.com/file.zip", win)
    check("ordinary links hide the quality row", plain.kind == "file" and not plain.form.isRowVisible(plain.quality))

    drm = AddDownloadDialog(win.settings, f"{base}/fairplay.m3u8", win)
    wait_until(app, lambda: drm._probe_error is not None)
    check("DRM is explained in the dialog", "DRM" in drm.stream_note.text(), drm.stream_note.text())

    # Browser-captured video without an extension in the URL, named after the page title.
    received = []
    real_add = win.add_download
    win.add_download = lambda url="", **prefill: received.append(prefill)
    win._incoming.append({"type": "download", "url": f"{base}/master.m3u8", "kind": "hls",
                          "title": "Big Buck Bunny"})
    win._process_incoming()
    win.add_download = real_add
    check("captured video uses the page title and kind",
          received and received[0]["filename"] == "Big Buck Bunny" and received[0]["kind"] == "hls",
          str(received))

    # Download through the manager as the dialog would, then render the window.
    task = win.manager.add(f"{base}/master.m3u8", str(out), 4, dlg.filename.text(), dlg.headers(),
                           kind=dlg.kind, variant=dlg.variant())
    other = win.manager.add(f"{base}/dash1/manifest.mpd", str(out), 4, "Paused DASH video", kind="dash")
    wait_until(app, lambda: other.status == Status.DOWNLOADING or not other.is_running, 15)
    win.manager.pause(other)
    wait_until(app, lambda: task.status == Status.COMPLETED and not other.is_running, 60)
    win.refresh()
    check("GUI-started stream completes at chosen quality",
          task.status == Status.COMPLETED and task.final_path.name == "My Video.mp4" and "180p" in task.quality,
          f"{task.status} {task.error or ''} {task.final_path} {task.quality}")
    win.table.selectRow(0)
    win.refresh()
    row_size = win.table.item(0, COL_SIZE).text()
    check("completed video shows its real size", row_size and not row_size.startswith("~"), row_size)
    check("details mention HLS and quality", "HLS" in win.lbl_info.text() and "180p" in win.lbl_info.text(),
          win.lbl_info.text())
    if SHOT:
        win.table.selectRow(1)
        win.refresh()
        win.grab().save(f"{SHOT}-window.png")
    win.manager.shutdown()
    win.ipc_server.close()
finally:
    shutil.rmtree(media, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)

print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
