"""Keeping yt-dlp current without reinstalling PyDM.

YouTube changes its site every few weeks and old yt-dlp versions stop working.
The yt-dlp bundled with PyDM can't be replaced in place (it's inside the app),
so newer versions are downloaded from PyPI into %LOCALAPPDATA%\\PyDM\\yt-dlp\\<version>
and put ahead of the bundled one on sys.path at startup (activate(), called from
pydm/__init__.py before anything imports yt_dlp).

An update is verified before it's used: wheel hashes must match PyPI's, and a
separate PyDM process must import it and load its site list. A newer PyDM whose
bundled yt-dlp is newer than the downloaded one simply ignores the download.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PYPI = "https://pypi.org/pypi"
FROZEN = getattr(sys, "frozen", False)
_active: dict = {}  # what activate() did: {"version": ..., "path": ...}


def root() -> Path:
    base = os.environ.get("PYDM_UPDATES_DIR") or os.path.join(os.environ.get("LOCALAPPDATA", str(Path.home())),
                                                             "PyDM", "yt-dlp")
    return Path(base)


def version_tuple(v: str) -> tuple:
    parts = []
    for p in v.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def bundled_version() -> str | None:
    """The yt-dlp that came with PyDM, read from its metadata (no import)."""
    from importlib import metadata
    try:
        return metadata.version("yt-dlp")
    except metadata.PackageNotFoundError:
        return None


def _current() -> tuple[str, Path] | None:
    try:
        version = (root() / "current.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    path = root() / version
    return (version, path) if version and (path / "yt_dlp").is_dir() else None


def activate() -> None:
    """Use a downloaded yt-dlp if it's newer than the bundled one."""
    if os.environ.get("PYDM_NO_YTDLP_UPDATE") or "yt_dlp" in sys.modules:
        return
    current = _current()
    if not current:
        return
    version, path = current
    bundled = bundled_version()
    if bundled and version_tuple(bundled) >= version_tuple(version):
        return  # this PyDM already ships the same or newer
    sys.path.insert(0, str(path))
    _active.update(version=version, path=str(path))


def active_version() -> str:
    """The yt-dlp version this process uses (imports yt_dlp's small version module)."""
    if _active:
        return _active["version"]
    return bundled_version() or "unknown"


# --- checking and installing --------------------------------------------------------

def _get(url: str, timeout: float = 30) -> bytes:
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "PyDM updater"})
    r.raise_for_status()
    return r.content


def latest() -> dict:
    """Newest yt-dlp on PyPI and the yt-dlp-ejs version it pins (its YouTube
    challenge solver): {"version", "files": [(name, url, sha256), ...]}."""
    info = json.loads(_get(f"{PYPI}/yt-dlp/json"))
    version = info["info"]["version"]
    files = [_wheel(info, "yt-dlp")]
    for req in info["info"].get("requires_dist") or []:
        name, _, rest = req.partition("==")
        if name.strip() == "yt-dlp-ejs" and rest:
            ejs_version = rest.split(";")[0].strip()
            files.append(_wheel(json.loads(_get(f"{PYPI}/yt-dlp-ejs/{ejs_version}/json")), "yt-dlp-ejs"))
            break
    return {"version": version, "files": files}


def _wheel(info: dict, name: str) -> tuple[str, str, str]:
    for f in info["urls"]:
        if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-none-any.whl"):
            return f["filename"], f["url"], f["digests"]["sha256"]
    raise RuntimeError(f"No installable {name} package on PyPI")


def check(force: bool = False) -> dict:
    """Compare with PyPI. Returns {"current", "latest", "newer": bool}."""
    newest = latest()
    current = (_current() or (None, None))[0]
    best = max([v for v in (current, bundled_version()) if v] or ["0"], key=version_tuple)
    return {"current": best, "latest": newest["version"], "newer": version_tuple(newest["version"]) >
            version_tuple(best), "files": newest["files"]}


def install(release: dict) -> str:
    """Download, verify and switch to a release from check()/latest(). Returns its version."""
    version = release["latest"] if "latest" in release else release["version"]
    target = root() / version
    staging = Path(tempfile.mkdtemp(prefix=f"{version}.", dir=_ensure_root()))
    try:
        for filename, url, sha256 in release["files"]:
            data = _get(url, timeout=120)
            if hashlib.sha256(data).hexdigest() != sha256:
                raise RuntimeError(f"{filename} didn't match PyPI's checksum")
            with zipfile.ZipFile(io.BytesIO(data)) as wheel:
                wheel.extractall(staging)
        ok, detail = self_test(staging)
        if not ok:
            raise RuntimeError(f"The new yt-dlp didn't start: {detail}")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    (root() / "current.txt").write_text(version, encoding="utf-8")
    _cleanup(keep={version, _active.get("version")})
    return version


def _ensure_root() -> Path:
    root().mkdir(parents=True, exist_ok=True)
    return root()


def _cleanup(keep: set) -> None:
    for d in root().iterdir():
        if d.is_dir() and d.name not in keep:
            shutil.rmtree(d, ignore_errors=True)  # in use by this process? Windows keeps it; fine


def self_test(path: Path) -> tuple[bool, str]:
    """Import yt-dlp from `path` in a separate PyDM process (so a broken download
    can't affect this one) and load its list of sites."""
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    out = Path(name)
    cmd = [sys.executable] if FROZEN else [sys.executable, "-m", "pydm"]
    env = {**os.environ, "PYDM_NO_YTDLP_UPDATE": "1"}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        cwd = None if FROZEN else str(Path(__file__).resolve().parent.parent)  # for `-m pydm`
        subprocess.run([*cmd, "--check-ytdlp", str(path), str(out)], env=env, timeout=120, creationflags=flags,
                       cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        result = json.loads(out.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return False, str(e)
    finally:
        out.unlink(missing_ok=True)
    return bool(result.get("ok")), result.get("error") or result.get("version", "")


def run_self_test(path: str, out: str) -> None:
    """The child side of self_test(): `PyDM --check-ytdlp PATH OUT`."""
    result = {"ok": False}
    try:
        sys.path.insert(0, path)
        import yt_dlp
        import yt_dlp.version
        from yt_dlp.extractor import gen_extractor_classes
        result["version"] = yt_dlp.version.__version__
        result["sites"] = sum(1 for _ in gen_extractor_classes())
        result["from"] = os.path.dirname(yt_dlp.__file__)
        with yt_dlp.YoutubeDL({"quiet": True}) as _:
            pass
        result["ok"] = result["sites"] > 100 and os.path.normcase(result["from"]).startswith(os.path.normcase(path))
        if not result["ok"]:
            result["error"] = f"loaded from {result['from']} with {result['sites']} sites"
    except Exception as e:  # noqa: BLE001 - reported to the parent
        result["error"] = f"{e.__class__.__name__}: {e}"
    Path(out).write_text(json.dumps(result), encoding="utf-8")
