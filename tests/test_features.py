"""Speed limit, clipboard monitoring, folders by type, duplicate detection and
yt-dlp site detection. No internet needed. Run from the project root:
    .venv/Scripts/python tests/test_features.py
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
sys.path[:0] = [ROOT, os.path.dirname(os.path.abspath(__file__))]

import server  # noqa: E402
from PyQt6.QtCore import QStandardPaths  # noqa: E402
from PyQt6.QtGui import QGuiApplication  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import engine, gui  # noqa: E402
from pydm.engine import DownloadTask, Status  # noqa: E402
from pydm.gui import AddDownloadDialog, MainWindow, url_key  # noqa: E402
from pydm.streams import guess_kind  # noqa: E402
from pydm.ytdl import site_for  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


app = QApplication([])
app.setApplicationName("PyDM-features-test")
app.setOrganizationName("PyDM-features-test")
base = server.start()
out = Path(tempfile.mkdtemp())


def pump(seconds=0.1):
    t0 = time.time()
    while time.time() - t0 < seconds:
        app.processEvents()
        time.sleep(0.01)


# --- speed limit -------------------------------------------------------------------
SIZE = len(server.DATA)  # bytes served at /ranges/x


def timed_download(name, limit_bps, lift_after=None):
    engine.LIMITER.set_rate(limit_bps)
    t = DownloadTask(f"{base}/ranges/x", out, 8, name)
    t0 = time.time()
    t.start()
    if lift_after:
        time.sleep(lift_after)
        engine.LIMITER.set_rate(0)
    t.join(60)
    return t, time.time() - t0


t, free = timed_download("free.bin", 0)
check("unlimited download is fast", t.status == Status.COMPLETED and free < 3, f"{free:.2f}s")
t, limited = timed_download("limited.bin", 4 * 1024 * 1024)
expected = SIZE / (4 * 1024 * 1024)
check("4 MB/s limit holds across 8 connections", t.status == Status.COMPLETED
      and expected - 0.7 <= limited <= expected + 1.5, f"{limited:.2f}s, expected ~{expected:.1f}s")
t, lifted = timed_download("lifted.bin", 512 * 1024, lift_after=1.0)
check("lifting the limit speeds a running download back up", t.status == Status.COMPLETED and lifted < 4,
      f"{lifted:.2f}s (would be ~24s at 512 KB/s)")
engine.LIMITER.set_rate(0)

# --- link identity and video sites ----------------------------------------------------
check("YouTube links match by video id", url_key("https://www.youtube.com/watch?v=aqz-KE-bpKQ&t=30")
      == url_key("https://youtu.be/aqz-KE-bpKQ") == url_key("https://m.youtube.com/shorts/aqz-KE-bpKQ"))
check("other links match by host/path/query", url_key("https://A.example/f.zip?x=1#top")
      == url_key("https://a.example/f.zip?x=1") != url_key("https://a.example/f.zip?x=2"))
sites = {u: site_for(u) for u in ("https://vimeo.com/76979871", "https://x.com/a/status/1",
                                  "https://www.facebook.com/watch/?v=1", "https://www.twitch.tv/videos/1")}
check("yt-dlp video sites recognised", all(sites.values()) and all(guess_kind(u) == "ytdl" for u in sites), str(sites))
check("file links and file hosts stay plain downloads",
      guess_kind("https://www.dropbox.com/s/abc/file.zip?dl=0") is None
      and guess_kind("https://vimeo.com/files/setup.exe") is None and guess_kind("https://example.com/page") is None)

# --- qualities for different kinds of sites (yt-dlp's answer faked, no network) ---
from pydm import ytdl  # noqa: E402

real_extract = ytdl._extract
fake = {}
ytdl._extract = lambda url, headers: fake[url]
fake["fb"] = {"title": "x", "formats": [{"format_id": "hd"}, {"format_id": "sd"}]}  # Facebook: no heights
labels = [v.label for v in ytdl.list_variants("fb")[1]]
check("Facebook-style formats: Best / HD / SD, no fake 'Audio only'", labels == ["Best quality", "HD", "SD"],
      str(labels))
check("…HD maps to that format", ytdl._format_for("fmt:hd").startswith("hd/"))
fake["yt"] = {"formats": [{"format_id": "1", "height": 720, "vcodec": "avc1"},
                          {"format_id": "2", "height": 1080, "vcodec": "avc1", "fps": 60},
                          {"format_id": "3", "vcodec": "none", "acodec": "mp4a"}]}
labels = [v.label for v in ytdl.list_variants("yt")[1]]
check("heights best first, audio only when there's an audio track", labels == ["1080p60", "720p", "Audio only (m4a)"],
      str(labels))
fake["carousel"] = {"_type": "playlist", "webpage_url": "https://www.instagram.com/p/X/",
                    "entries": [{"title": "a"}, {"title": "b"}, {"title": "c"}]}
variants = ytdl.list_variants("carousel")[1]
check("a post with several videos lists them", [v.id for v in variants] == ["item:1", "item:2", "item:3"]
      and variants[1].label.startswith("Video 2 of 3"), str([v.label for v in variants]))
fake["ytlist"] = {"_type": "playlist", "webpage_url": "https://www.youtube.com/playlist?list=1", "entries": [{}]}
try:
    ytdl.list_variants("ytlist")
    check("YouTube playlists still refused", False)
except Exception as e:  # noqa: BLE001
    check("YouTube playlists still refused", "playlist" in str(e).lower())
ytdl._extract = real_extract


class FakeError(Exception):
    pass


msg = ytdl._clean(FakeError("ERROR: [Instagram] C2o: Instagram sent an empty media response. Check if this post is "
                            "accessible in your browser without being logged-in. If it is not, then use "
                            "--cookies-from-browser or --cookies for the authentication. See  https://github.com/"
                            "yt-dlp/yt-dlp/wiki/FAQ for how to pass cookies"))
check("login errors: yt-dlp's command-line advice replaced by what to do in PyDM",
      msg.startswith("Instagram sent an empty media response.") and "--cookies" not in msg
      and "Sign in to it in your browser" in msg, msg)

# --- window features ----------------------------------------------------------------------
win = MainWindow()
win.settings.clear()
win.manager.tasks.clear()
win.settings.setValue("download_dir", str(out))
win.show()

win.set_speed_limit(1024)
check("speed-limit button and engine follow the setting", engine.LIMITER.rate == 1024 * 1024
      and win.limit_button.text() == "Speed limit: 1 MB/s", win.limit_button.text())
win.set_speed_limit(0)
check("…and 'Unlimited' turns it off", engine.LIMITER.rate == 0 and win.limit_button.text().endswith("Unlimited"))

# Folders by type
folders = {}
for url, name in (("https://a.example/setup.zip", ""), ("https://a.example/clip.mp4", ""),
                  ("https://a.example/get?id=1", "report.pdf"), ("https://a.example/get?id=2", ""),
                  ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", "")):
    dlg = AddDownloadDialog(win.settings, url, win, filename=name)
    folders[url.rsplit("/", 1)[-1]] = Path(dlg.folder.text()).relative_to(out).as_posix()
check("folders by type", folders == {"setup.zip": "Compressed", "clip.mp4": "Video", "get?id=1": "Documents",
                                     "get?id=2": ".", "watch?v=aqz-KE-bpKQ": "Video"}, str(folders))
dlg = AddDownloadDialog(win.settings, "https://a.example/song.mp3", win)
dlg.folder.setText(str(out / "Mine"))
dlg._folder_auto = False  # as typing in the field does
dlg.filename.setText("other.zip")
check("a folder the user picked isn't overridden", dlg.folder.text() == str(out / "Mine"))
win.settings.setValue("sort_by_type", False)
check("sorting can be turned off", AddDownloadDialog(win.settings, "https://a.example/s.zip", win).folder.text()
      == str(out))
win.settings.setValue("sort_by_type", True)

# Duplicates
shown = []
real_exec = AddDownloadDialog.exec


def fake_exec(self):
    shown.append(self.url.text())
    return QDialog.DialogCode.Rejected


AddDownloadDialog.exec = fake_exec
existing = win.manager.add(f"{base}/ranges/x?delay=0.05", out, 2, filename="dup.bin")
pump(0.5)
win.manager.pause(existing)
existing.join(5)
asked = []
win.ask_duplicate = lambda task: asked.append(task) or "cancel"
win.add_download(f"{base}/ranges/x?delay=0.05")
check("duplicate link: asked first, Cancel adds nothing", asked == [existing] and not shown
      and len(win.manager.tasks) == 1)
win.ask_duplicate = lambda task: "again"
win.add_download(f"{base}/ranges/x?delay=0.05")
check("'Download again' continues to the Add dialog", shown == [f"{base}/ranges/x?delay=0.05"])
win.ask_duplicate = lambda task: "resume"
win.add_download(f"{base}/ranges/x?delay=0.05")
check("'Resume it' resumes the existing download", existing.status in (*Status.ACTIVE, Status.QUEUED,
                                                                         Status.COMPLETED), existing.status)
win.add_download(f"{base}/ranges/other")
check("new links go straight to the Add dialog", shown[-1] == f"{base}/ranges/other")
AddDownloadDialog.exec = real_exec

# Clipboard
offered = []
win.add_download = lambda url="", **kw: offered.append(url)
gui.QApplication.activeWindow = staticmethod(lambda: None)  # as if another program had focus
clip = QGuiApplication.clipboard()
for text in ("https://files.example/big.iso", "https://example.com/some/page", "not a link",
             "https://files.example/big.iso", "https://vimeo.com/76979871"):
    clip.setText(text)
    pump(0.15)
check("copied download links and video pages are offered once each",
      offered == ["https://files.example/big.iso", "https://vimeo.com/76979871"], str(offered))
win.settings.setValue("watch_clipboard", False)
clip.setText("https://files.example/other.zip")
pump(0.15)
check("clipboard watching can be turned off", len(offered) == 2)
win.settings.setValue("watch_clipboard", True)
win.table.selectRow(0)
win.copy_url()
pump(0.15)
check("links copied from PyDM itself aren't offered back", len(offered) == 2, str(offered))

win.manager.shutdown()
win.ipc_server.close()
print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
