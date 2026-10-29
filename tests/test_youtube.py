"""YouTube via yt-dlp: needs internet. Downloads Blender's "Big Buck Bunny"
(Creative Commons) at 144p, about 35 MB.
Run from the project root: .venv/Scripts/python tests/test_youtube.py [screenshot-prefix]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["PYDM_IPC_NAME"] = f"pydm-test-{uuid.uuid4().hex[:8]}"
SHOT = sys.argv[1] if len(sys.argv) > 1 else None
sys.path.insert(0, ROOT)

from PyQt6.QtCore import QStandardPaths  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import ytdl  # noqa: E402
from pydm.engine import DownloadManager, Status  # noqa: E402
from pydm.gui import AddDownloadDialog, MainWindow  # noqa: E402
from pydm.streams import find_ffmpeg, guess_kind  # noqa: E402

VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def wait_until(app, cond, timeout=60.0):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.05)
    return cond()


check("YouTube URLs detected", all(guess_kind(u) == "ytdl" for u in (
    VIDEO, "https://youtu.be/aqz-KE-bpKQ", "https://m.youtube.com/shorts/abc", "https://music.youtube.com/watch?v=x"))
    and guess_kind("https://notyoutube.com/watch?v=x") is None and guess_kind("https://a.example/v.m3u8") == "hls")

with tempfile.TemporaryDirectory() as tmp:
    ytdl._cookie_file(Path(tmp, "c.txt"), VIDEO, "SID=abc; PREF=f6=40000000")
    lines = Path(tmp, "c.txt").read_text().splitlines()
    check("cookies scoped to .youtube.com", lines[1].startswith(".youtube.com\t") and lines[2].endswith("\tPREF\tf6=40000000"),
          str(lines))

try:
    ytdl.list_variants("https://www.youtube.com/playlist?list=PL4cUxeGkcC9gcy9lrvMJ75z9maRw4byYp")
    check("playlists refused", False)
except Exception as e:  # noqa: BLE001
    check("playlists refused", "playlist" in str(e).lower(), str(e))

# Facebook (a public video from yt-dlp's own tests): formats without heights
FB = "https://www.facebook.com/facebook/videos/10153231379946729/"
try:
    labels = [v.label for v in ytdl.list_variants(FB)[1]]
    check("Facebook: Best / HD / SD offered", labels[:1] == ["Best quality"] and "HD" in labels, str(labels))
    fb_out = Path(tempfile.mkdtemp())
    fb = ytdl.YtdlTask(FB, fb_out, variant="best")
    fb.start()
    fb.join(300)
    info = subprocess.run([find_ffmpeg(), "-i", str(fb.final_path)], capture_output=True, text=True).stderr \
        if fb.status == Status.COMPLETED else ""
    check("Facebook: downloads with picture and sound", "Video: h264" in info and "Audio:" in info,
          f"{fb.status} {fb.error}")
    shutil.rmtree(fb_out, ignore_errors=True)
except Exception as e:  # noqa: BLE001 - Facebook may block anonymous access from some networks
    check("Facebook public video", False, str(e)[:200])

out = Path(tempfile.mkdtemp())
app = QApplication([])
app.setApplicationName("PyDM-youtube-test")
app.setStyle("Fusion")
try:
    win = MainWindow()
    win.manager.tasks.clear()
    win.settings.setValue("download_dir", str(out))
    win.show()

    dlg = AddDownloadDialog(win.settings, VIDEO, win)
    dlg.show()
    check("dialog recognises YouTube", dlg.kind == "ytdl" and dlg.form.isRowVisible(dlg.quality)
          and "video title" in dlg.filename.placeholderText())
    loaded = wait_until(app, lambda: dlg.quality.isEnabled())
    labels = [dlg.quality.itemText(i) for i in range(dlg.quality.count())]
    check("qualities listed best first, with audio only", loaded and labels[0].startswith("2160p")
          and "144p" in labels and labels[-1].startswith("Audio"), str(labels))
    check("note says YouTube", dlg.stream_note.text().startswith("YouTube video"), dlg.stream_note.text())
    dlg.quality.setCurrentIndex(labels.index("144p"))
    check("chosen quality is a height", dlg.variant() == "144", str(dlg.variant()))
    if SHOT:
        dlg.grab().save(f"{SHOT}-dialog.png")

    received = []
    real_add = win.add_download
    win.add_download = lambda url="", **prefill: received.append((url, prefill))
    win._incoming.append({"type": "download", "url": VIDEO, "kind": "ytdl", "title": "Big Buck Bunny - YouTube",
                          "cookies": "SID=abc"})
    win._process_incoming()
    win.add_download = real_add
    check("captured YouTube video: kind kept, yt-dlp picks the name",
          received and received[0][1]["kind"] == "ytdl" and received[0][1]["filename"] == "", str(received))

    task = win.manager.add(VIDEO, str(out), 4, None, {}, kind=dlg.kind, variant=dlg.variant())
    check("manager makes a YtdlTask", isinstance(task, ytdl.YtdlTask))
    wait_until(app, lambda: task.downloaded > 2_000_000, 90)
    win.refresh()
    s = task.snapshot()
    check("progress and estimated size while downloading", s["total_size"] and s["size_estimated"]
          and 0 < s["progress"] < 1 and s["filename"].startswith("Big Buck Bunny"), str({k: s[k] for k in
          ("status", "filename", "downloaded", "total_size", "progress")}))
    if SHOT:
        win.table.selectRow(0)
        win.refresh()
        win.grab().save(f"{SHOT}-window.png")

    # Pause, "restart the app" (a fresh manager reading the saved list), resume.
    win.manager.pause(task)
    task.join(30)
    win.manager.save()
    state = json.loads(win.manager.state_file.read_text(encoding="utf-8"))
    check("paused YouTube download saved", any(d["kind"] == "ytdl" and d["status"] == Status.PAUSED for d in state))
    win.manager.shutdown()
    again = DownloadManager(win.manager.state_file)
    task2 = next(t for t in again.tasks if t.id == task.id)
    check("reloaded as a YtdlTask with its name", isinstance(task2, ytdl.YtdlTask) and task2.filename == task.filename)
    again.resume(task2)
    wait_until(app, lambda: not task2.is_running and task2.status != Status.QUEUED, 300)
    check("resumed download completes", task2.status == Status.COMPLETED and task2.final_path.exists(),
          f"{task2.status} {task2.error}")
    check("no leftovers", sorted(p.name for p in out.iterdir()) == [task2.filename], str(list(out.iterdir())))
    if task2.status == Status.COMPLETED:
        probe = subprocess.run([find_ffmpeg(), "-v", "error", "-i", str(task2.final_path), "-f", "null", "-"],
                               capture_output=True, text=True)
        info = subprocess.run([find_ffmpeg(), "-i", str(task2.final_path)], capture_output=True, text=True).stderr
        check("output decodes cleanly with video and audio", probe.returncode == 0 and not probe.stderr.strip()
              and "Video: h264" in info and "Audio:" in info and "Duration: 00:10:3" in info)
    again.shutdown()
    win.ipc_server.close()
finally:
    shutil.rmtree(out, ignore_errors=True)

print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
