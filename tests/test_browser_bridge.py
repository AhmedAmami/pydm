"""Browser bridge test: talks to the native host exactly the way the browser
does (length-prefixed JSON over stdio) and checks the GUI receives the download.
Run from the project root: .venv/Scripts/python tests/test_browser_bridge.py
"""
import json
import os
import struct
import subprocess
import sys
import threading
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYDM_IPC_NAME"] = f"pydm-test-{uuid.uuid4().hex[:8]}"  # never hit a real window
sys.path.insert(0, ROOT)

from PyQt6.QtCore import QStandardPaths, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

QStandardPaths.setTestModeEnabled(True)
from pydm.gui import AddDownloadDialog, MainWindow  # noqa: E402

HOST_CMD = [sys.executable, "-m", "pydm.native_host"]
failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def talk_to_host(*messages, cmd=HOST_CMD):
    """Send messages like the browser does; return the decoded replies."""
    data = b"".join(struct.pack("<I", len(b)) + b for b in (json.dumps(m).encode() for m in messages))
    out = subprocess.run(cmd, input=data, capture_output=True, cwd=ROOT, timeout=30).stdout
    replies = []
    while out:
        (n,) = struct.unpack("<I", out[:4])
        replies.append(json.loads(out[4:4 + n]))
        out = out[4 + n:]
    return replies


app = QApplication([])
app.setApplicationName("PyDM-bridge-test")
win = MainWindow()
win.manager.tasks.clear()
received = []
win.add_download = lambda url="", **prefill: received.append((url, prefill))  # don't block on a modal dialog
results = {}


def scenario():
    results["ping"] = talk_to_host({"type": "ping"})
    results["download"] = talk_to_host({
        "type": "download", "url": "https://example.com/files/big.iso", "filename": "big.iso",
        "referrer": "https://example.com/page", "cookies": "sid=abc; theme=dark",
        "userAgent": "TestBrowser/1.0", "fileSize": 123456789})
    results["bad"] = talk_to_host({"type": "download", "url": "javascript:alert(1)"},
                                  {"type": "nonsense"})
    # A second launch with a URL should hand off to this window and exit.
    t0 = time.time()
    p = subprocess.run([sys.executable, "-m", "pydm", "https://example.com/second.zip"],
                       cwd=ROOT, capture_output=True, timeout=30)
    results["second_launch"] = (p.returncode, time.time() - t0)
    time.sleep(0.5)
    QTimer.singleShot(0, app.quit)


threading.Thread(target=scenario, daemon=True).start()
QTimer.singleShot(60000, app.quit)  # safety net
app.exec()

ping = results.get("ping", [{}])[0]
check("ping replies", ping.get("ok") is True and ping.get("gui_running") is True, str(ping))
check("download accepted", results.get("download") == [{"ok": True}], str(results.get("download")))
bad = results.get("bad", [])
check("non-http URL rejected", len(bad) == 2 and not bad[0]["ok"] and not bad[1]["ok"], str(bad))
code, secs = results.get("second_launch", (None, 0))
check("second launch hands off and exits", code == 0 and secs < 15, f"exit={code} in {secs:.1f}s")

urls = [u for u, _ in received]
check("GUI received both downloads", urls == ["https://example.com/files/big.iso", "https://example.com/second.zip"], str(urls))
if received:
    url, prefill = received[0]
    check("prefill carries browser context",
          prefill.get("filename") == "big.iso" and prefill.get("referer") == "https://example.com/page"
          and prefill.get("cookies") == "sid=abc; theme=dark"
          and prefill.get("extra_headers") == {"User-Agent": "TestBrowser/1.0"}
          and "117.74 MB" in prefill.get("source", ""), str(prefill))
    dlg = AddDownloadDialog(win.settings, url, win, **prefill)
    check("dialog builds request headers", dlg.headers() == {
        "User-Agent": "TestBrowser/1.0", "Referer": "https://example.com/page", "Cookie": "sid=abc; theme=dark"},
        str(dlg.headers()))
    check("dialog prefilled filename", dlg.filename.text() == "big.iso")

# Exercise the real launcher the browser runs, if it has been installed.
bat = os.path.join(ROOT, "native_host", "pydm_host.bat")
if os.path.exists(bat):
    r = talk_to_host({"type": "ping"}, cmd=["cmd", "/c", bat])
    check("installed .bat launcher speaks the protocol", r and r[0].get("ok") is True, str(r))
else:
    print("[SKIP] .bat launcher not installed yet")

win.ipc_server.close()
print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
