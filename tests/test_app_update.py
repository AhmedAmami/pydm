"""Updating PyDM itself (pydm/app_update.py and the Update dialog) against a fake
GitHub on the local test server: no real release is downloaded or installed.
Run from the project root: .venv/Scripts/python tests/test_app_update.py
"""
import hashlib
import json
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

work = Path(tempfile.mkdtemp())
os.environ["LOCALAPPDATA"] = str(work / "local")  # app_update.folder() lives under it
server.STATIC_ROOT = work / "site"
server.STATIC_ROOT.mkdir()
base = server.start()
os.environ["PYDM_RELEASES_API"] = f"{base}/static/latest.json"
os.environ["PYDM_RELEASES_DOWNLOADS"] = f"{base}/static/download/"

from PyQt6.QtCore import QStandardPaths  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import __version__, app_update, theme  # noqa: E402
from pydm.gui import AppUpdateDialog, MainWindow  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def publish(version, data=b"", digest=None, url=None, name=None):
    """Write a fake GitHub "latest release" answer and its installer."""
    name = name or f"PyDM-Setup-{version}.exe"
    (server.STATIC_ROOT / "download").mkdir(exist_ok=True)
    (server.STATIC_ROOT / "download" / name).write_bytes(data)
    asset = {"name": name, "size": len(data), "browser_download_url": url or f"{base}/static/download/{name}"}
    if digest is not False:
        asset["digest"] = "sha256:" + (digest or hashlib.sha256(data).hexdigest())
    (server.STATIC_ROOT / "latest.json").write_text(json.dumps(
        {"tag_name": f"v{version}", "html_url": "https://example.invalid/release", "assets": [asset]}))


def raises(f, text):
    try:
        f()
    except Exception as e:  # noqa: BLE001
        return text.lower() in str(e).lower()
    return False


# --- the module --------------------------------------------------------------------
payload = os.urandom(3 * 1024 * 1024 + 7)
publish("99.0.0", payload)
release = app_update.check()
check("newer release found", release["newer"] and release["version"] == "99.0.0", str(release))
seen = []
path = app_update.download(release, lambda d, t: seen.append((d, t)))
check("installer downloaded and verified", path.read_bytes() == payload and path.parent == app_update.folder())
check("progress reported up to the full size", seen and seen[-1] == (len(payload), len(payload)))
check("a second download reuses the verified file", app_update.download(release) == path)

publish(__version__, b"same")
check("same version is not newer", not app_update.check()["newer"])
publish("1.0.0", b"old")
check("older version is not newer", not app_update.check()["newer"])

publish("99.0.1", payload, digest="0" * 64)
bad = app_update.check()
check("checksum mismatch rejected", raises(lambda: app_update.download(bad), "checksum"))
check("rejected download leaves nothing behind", not list(app_update.folder().glob("*99.0.1*")))
publish("99.0.2", payload, digest=False)
check("release without a checksum refused", raises(app_update.check, "checksum"))
publish("99.0.3", payload, url="https://evil.example/PyDM-Setup-99.0.3.exe")
check("download from elsewhere refused", raises(app_update.check, "unexpected"))
publish("99.0.4", payload, name="something-else.zip")
check("release without an installer refused", raises(app_update.check, "no installer"))

publish("99.0.5", payload)
r5 = app_update.check()
check("cancel stops the download", raises(lambda: app_update.download(r5, cancelled=lambda: True), "cancelled")
      and not list(app_update.folder().glob("*99.0.5*")))

old = app_update.folder() / f"PyDM-Setup-{__version__}.exe"
old.write_bytes(b"x")
app_update.cleanup()
check("cleanup removes the installer of this version", not old.exists())
check("cleanup keeps a newer one", path.exists())

# --- the window --------------------------------------------------------------------
app = QApplication([])
app.setApplicationName("PyDM-appupdate-test")
app.setOrganizationName("PyDM-appupdate-test")
app.setStyle("Fusion")
theme.apply(app, "light")


def wait(cond, timeout=20):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.02)
    return cond()


win = MainWindow()
win.settings.clear()
win.show()
publish("99.1.0", payload)
check("no automatic check in tests", (win.check_app_update(auto=True), not win._app_busy)[1])
win.check_app_update()
check("button appears for a newer PyDM", wait(lambda: win.app_update_button.isVisible()),
      win.app_update_button.text())
check("button names the version", "99.1.0" in win.app_update_button.text())

installed = []
win.restart_app = lambda installer=None: installed.append(installer)
app_update.FROZEN = True
dlg = AppUpdateDialog(win, win._app_release)
dlg.show()
dlg.start()
check("Update now downloads and hands the installer over", wait(lambda: installed))
check("handed over the verified file", installed and Path(installed[0]).read_bytes() == payload
      and installed[0].name == "PyDM-Setup-99.1.0.exe")

publish("99.2.0", payload, digest="0" * 64)
win.check_app_update()
wait(lambda: not win._app_busy and win._app_release["version"] == "99.2.0")
installed.clear()
dlg = AppUpdateDialog(win, win._app_release)
dlg.show()
dlg.start()
check("bad download shown in the dialog", wait(lambda: "checksum" in dlg.status.text()), dlg.status.text())
check("nothing installed after a bad download", not installed and dlg.go.isEnabled()
      and dlg.go.text() == "Try again")
dlg.close()

win._quitting = True
win.close()
print(f"\n{'ALL PASSED' if not failures else 'FAILED: ' + ', '.join(failures)}")
sys.exit(1 if failures else 0)
