"""Component check, for Settings → About and for verifying a packaged build:

    PyDM.exe --build-check OUT_DIR      writes OUT_DIR/build-check.json

The build check also downloads a short YouTube video (Blender's Creative Commons
"Big Buck Bunny", 144p, ~35 MB), which exercises yt-dlp, Deno and ffmpeg
inside the bundle, then decodes the result.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from . import __version__

TEST_VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"


def _version_line(cmd: list[str]) -> str:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=flags)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (OSError, IndexError, subprocess.SubprocessError) as e:
        return f"error: {e}"


def components() -> dict:
    from PyQt6.QtCore import QT_VERSION_STR

    from .streams import find_ffmpeg
    from .ytdl import _js_runtime
    info = {"PyDM": __version__, "Python": platform.python_version(), "Qt": QT_VERSION_STR}
    try:
        import yt_dlp.version
        from .ytdl import _extractors
        info["yt-dlp"] = f"{yt_dlp.version.__version__} ({len(_extractors()):,} video sites)"
    except ImportError:
        info["yt-dlp"] = "missing"
    ffmpeg = find_ffmpeg()
    info["ffmpeg"] = _version_line([ffmpeg, "-version"]).split(" Copyright")[0] if ffmpeg else "missing"
    deno = _js_runtime().get("deno", {}).get("path")
    info["Deno"] = _version_line([deno, "--version"]) if deno else "missing"
    return info


def _window(out: Path) -> dict:
    """Build the main window off-screen, isolated from the user's real PyDM
    (test-mode data folder, its own settings and pipe name), and screenshot it."""
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))
    from PyQt6.QtCore import QStandardPaths
    from PyQt6.QtWidgets import QApplication

    from . import ipc, theme
    QStandardPaths.setTestModeEnabled(True)
    ipc.SERVER_NAME = f"pydm-buildcheck-{os.getpid()}"
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("PyDM-buildcheck")
    app.setOrganizationName("PyDM-buildcheck")
    app.setStyle("Fusion")
    from .gui import MainWindow
    theme.apply(app, "light")
    win = MainWindow()
    win.apply_icons()
    win.resize(1100, 640)
    win.show()
    app.processEvents()
    shot = out / "window.png"
    ok = win.grab().save(str(shot))
    icon_ok = not theme.app_icon().isNull() and not theme.icon("plus").isNull()
    win.manager.shutdown()
    win.ipc_server.close()
    return {"screenshot": str(shot) if ok else None, "icons": icon_ok, "style": app.style().name()}


def run(out_dir: str) -> None:
    from .engine import Status
    from .streams import find_ffmpeg
    from .ytdl import YtdlTask
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result: dict = {"frozen": bool(getattr(sys, "frozen", False)), "components": components()}
    try:
        result["window"] = _window(out)
    except Exception as e:  # noqa: BLE001 - reported in the JSON
        result["window"] = {"error": repr(e)}
    task = YtdlTask(TEST_VIDEO, out, variant="144")
    t0 = time.time()
    task.start()
    task.join(600)
    result["download"] = {"status": task.status, "error": task.error, "file": task.filename,
                          "size": task.total_size, "quality": task.quality, "seconds": round(time.time() - t0, 1)}
    if task.status == Status.COMPLETED:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        decode = subprocess.run([find_ffmpeg(), "-v", "error", "-i", str(task.final_path), "-f", "null", "-"],
                                capture_output=True, text=True, creationflags=flags)
        result["decode"] = {"ok": decode.returncode == 0 and not decode.stderr.strip(), "errors": decode.stderr[-500:]}
    (out / "build-check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
