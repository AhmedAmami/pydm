"""The yt-dlp updater (pydm/updates.py) against a fake PyPI on the local test
server, so no real download happens and the real update folder isn't touched.
The "new" yt-dlp is the installed one with its version bumped to 2099.1.1.
Run from the project root: .venv/Scripts/python tests/test_updates.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
work = Path(tempfile.mkdtemp())
os.environ["PYDM_UPDATES_DIR"] = str(work / "updates")  # before pydm is imported

import server  # noqa: E402
import yt_dlp_ejs  # noqa: E402
from pydm import updates  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def site_packages_dir(pkg):
    import importlib.util
    return Path(importlib.util.find_spec(pkg).origin).parent


def make_wheel(path: Path, package: str, dist: str, version: str, patch=None):
    src = site_packages_dir(package)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in src.rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts:
                rel = f"{package}/{f.relative_to(src).as_posix()}"
                data = f.read_bytes()
                if patch:
                    data = patch(rel, data)
                z.writestr(rel, data)
        z.writestr(f"{dist.replace('-', '_')}-{version}.dist-info/METADATA",
                   f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bump(rel, data):
    if rel == "yt_dlp/version.py":
        return b"__version__ = '2099.1.1'\nRELEASE_GIT_HEAD = ''\nVARIANT = None\nUPDATE_HINT = None\n" \
               b"CHANNEL = 'stable'\nORIGIN = 'yt-dlp/yt-dlp'\n_pkg_version = '2099.1.1'\n"
    return data


def broken(rel, data):
    data = bump(rel, data)
    return data + b"\nraise ImportError('simulated broken release')\n" if rel == "yt_dlp/__init__.py" else data


files = work / "pypi"
(files / "yt-dlp").mkdir(parents=True)
(files / "yt-dlp-ejs" / "0.8.0").mkdir(parents=True)
server.STATIC_ROOT = work
base = server.start()
updates.PYPI = f"{base}/static/pypi"


def publish(version, wheel_patch, sha_override=None):
    name = f"yt_dlp-{version}-py3-none-any.whl"
    sha = make_wheel(files / name, "yt_dlp", "yt-dlp", version, wheel_patch)
    (files / "yt-dlp" / "json").write_text(json.dumps({
        "info": {"version": version, "requires_dist": ['yt-dlp-ejs==0.8.0; extra == "default"']},
        "urls": [{"filename": name, "packagetype": "bdist_wheel", "url": f"{base}/static/pypi/{name}",
                  "digests": {"sha256": sha_override or sha}}]}))


ejs_sha = make_wheel(files / "yt_dlp_ejs-0.8.0-py3-none-any.whl", "yt_dlp_ejs", "yt-dlp-ejs", "0.8.0")
(files / "yt-dlp-ejs" / "0.8.0" / "json").write_text(json.dumps({"info": {"version": "0.8.0"}, "urls": [
    {"filename": "yt_dlp_ejs-0.8.0-py3-none-any.whl", "packagetype": "bdist_wheel",
     "url": f"{base}/static/pypi/yt_dlp_ejs-0.8.0-py3-none-any.whl", "digests": {"sha256": ejs_sha}}]}))


def version_in_new_process():
    env = {**os.environ}
    out = subprocess.run([sys.executable, "-c", "import pydm, yt_dlp.version, pydm.ytdl as y; "
                          "print(yt_dlp.version.__version__, y.site_for('https://vimeo.com/1'))"],
                         capture_output=True, text=True, cwd=ROOT, env=env)
    return out.stdout.strip() or out.stderr.strip()[-300:]


bundled = updates.bundled_version()
check("nothing downloaded yet: the bundled yt-dlp is used", version_in_new_process() == f"{bundled} Vimeo",
      version_in_new_process())

# A bad checksum is refused
publish("2099.1.1", bump, sha_override="0" * 64)
release = updates.check()
check("check() sees the newer version", release["newer"] and release["latest"] == "2099.1.1", str(release)[:120])
try:
    updates.install(release)
    check("wrong checksum refused", False)
except RuntimeError as e:
    check("wrong checksum refused", "checksum" in str(e), str(e))

# A release that crashes on import is refused by the self-test
publish("2099.1.1", broken)
try:
    updates.install(updates.check())
    check("a broken release is refused", False)
except RuntimeError as e:
    check("a broken release is refused (tested in a separate process)", "didn't start" in str(e), str(e)[:160])
check("…nothing switched, no leftovers", not (updates.root() / "current.txt").exists()
      and not any(p.is_dir() for p in updates.root().iterdir()), str(list(updates.root().iterdir())))

# A good release is installed and used by the next PyDM start
publish("2099.1.1", bump)
version = updates.install(updates.check())
check("good release installed", version == "2099.1.1" and (updates.root() / "2099.1.1" / "yt_dlp").is_dir())
check("the next start uses it (and its site list works)", version_in_new_process() == "2099.1.1 Vimeo",
      version_in_new_process())
check("already up to date afterwards", not updates.check()["newer"])

# An older download than the bundled one is ignored (e.g. after installing a newer PyDM)
(updates.root() / "current.txt").write_text("2000.1.1")
(updates.root() / "2000.1.1" / "yt_dlp").mkdir(parents=True)
check("a download older than the bundled yt-dlp is ignored", version_in_new_process() == f"{bundled} Vimeo",
      version_in_new_process())

# --- the window: Check for updates, the restart button, restarting with downloads running ---
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
os.environ["PYDM_IPC_NAME"] = "pydm-test-updates"
import time  # noqa: E402

from PyQt6.QtCore import QStandardPaths  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm import gui  # noqa: E402
from pydm.engine import Status  # noqa: E402

app = QApplication([])
app.setApplicationName("PyDM-updates-test")
app.setOrganizationName("PyDM-updates-test")


def wait(cond, timeout=120):
    t0 = time.time()
    while not cond() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.05)
    return cond()


publish("2099.2.2", lambda rel, data: bump(rel, data).replace(b"2099.1.1", b"2099.2.2"))
win = gui.MainWindow()
win.settings.clear()
win.manager.tasks.clear()
settings = gui.SettingsDialog(win.settings, win, "about")
about = settings.tabs.widget(3)
check("About: no restart button before an update", about.restart_btn.isHidden())
about.check_btn.click()
check("Check for updates installs the newer yt-dlp", wait(lambda: win._update_ready == "2099.2.2")
      and (updates.root() / "current.txt").read_text() == "2099.2.2", str(win._update_last))
app.processEvents()
check("…the About page and status bar offer a restart", not about.restart_btn.isHidden()
      and not win.update_button.isHidden() and "2099.2.2" in win.update_button.text(), about.update_status.text())
settings.close()

task = win.manager.add(f"{base}/ranges/x?delay=0.05", work / "dl", 2, filename="running.bin")
wait(lambda: task.status == Status.DOWNLOADING, 20)
launched, quit_called = [], []
gui.ipc.launch_gui = lambda: launched.append(True)
win.quit_app = lambda: quit_called.append(True)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
win.restart_app()
check("Restart pauses downloads, starts a new PyDM and quits", launched and quit_called
      and task.status == Status.PAUSED)
win2 = gui.MainWindow()
app.processEvents()
resumed = win2.manager.get(task.id)
check("…and the new PyDM continues them", resumed is not None and resumed.status in (*Status.ACTIVE, Status.QUEUED),
      resumed and resumed.status)
win2.manager.shutdown()

print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
