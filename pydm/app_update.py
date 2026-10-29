"""Updating PyDM itself from its GitHub releases.

check() asks GitHub for the latest release; download() fetches its setup .exe into
%LOCALAPPDATA%\\PyDM\\setup and checks it against the SHA-256 GitHub publishes for
it; run_installer() starts it silently. The installer closes PyDM if it's still
running, replaces the files (settings, downloads and queues are kept) and, because
of /RELAUNCH=1, starts PyDM again (see packaging/pydm.iss).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

from . import __version__
from .updates import version_tuple

REPO = "AhmedAmami/pydm"
API = os.environ.get("PYDM_RELEASES_API", f"https://api.github.com/repos/{REPO}/releases/latest")
DOWNLOADS = os.environ.get("PYDM_RELEASES_DOWNLOADS", f"https://github.com/{REPO}/releases/download/")
PAGE = f"https://github.com/{REPO}/releases/latest"
SETUP = re.compile(r"PyDM-Setup-([\d.]+)\.exe")
FROZEN = getattr(sys, "frozen", False)


def folder() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PyDM" / "setup"


def check() -> dict:
    """{"version", "newer": bool, "url", "sha256", "size", "page"} for the latest release."""
    import requests
    r = requests.get(API, timeout=30, headers={"User-Agent": "PyDM updater",
                                               "Accept": "application/vnd.github+json"})
    r.raise_for_status()
    info = r.json()
    version = info["tag_name"].lstrip("vV")
    asset = next((a for a in info.get("assets") or [] if SETUP.fullmatch(a["name"])), None)
    if not asset:
        raise RuntimeError(f"PyDM {version} has no installer attached")
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise RuntimeError(f"GitHub didn't publish a checksum for PyDM {version}")
    url = asset["browser_download_url"]
    if not url.startswith(DOWNLOADS):
        raise RuntimeError(f"Unexpected download location: {url}")
    return {"version": version, "newer": version_tuple(version) > version_tuple(__version__), "url": url,
            "sha256": digest.split(":", 1)[1].lower(), "size": asset.get("size") or 0,
            "page": info.get("html_url") or PAGE}


def download(release: dict, progress=None, cancelled=lambda: False) -> Path:
    """Download and verify the installer. progress(done, total) is called as it goes."""
    import requests
    target = folder() / f"PyDM-Setup-{release['version']}.exe"
    if target.exists() and _sha256(target) == release["sha256"]:
        return target  # downloaded before, e.g. "Later" then "Update" again
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".part")
    h = hashlib.sha256()
    done = 0
    try:
        with requests.get(release["url"], stream=True, timeout=60, headers={"User-Agent": "PyDM updater"}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or release["size"] or 0)
            with open(part, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    if cancelled():
                        raise InterruptedError("Cancelled")
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if h.hexdigest() != release["sha256"]:
            raise RuntimeError("The download didn't match GitHub's checksum, so it wasn't used")
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    return target


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_installer(path: Path) -> None:
    """Start the installer, detached so it outlives this process."""
    cmd = [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"]
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:  # not allowed to leave the job: plain detached start
        subprocess.Popen(cmd, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def cleanup() -> None:
    """Remove installers for this version or older (left over after an update)."""
    try:
        for f in folder().iterdir():
            m = SETUP.fullmatch(f.name)
            if f.suffix == ".part" or m and version_tuple(m.group(1)) <= version_tuple(__version__):
                f.unlink(missing_ok=True)
    except OSError:
        pass
