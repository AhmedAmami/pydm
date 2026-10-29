"""Segmented HTTP download engine.

No GUI dependencies. A DownloadTask probes the server, splits the file into
byte ranges, fetches them over parallel connections, and can pause/resume via
a JSON sidecar next to the partial file. When a connection finishes early it
"steals" half of the largest remaining segment, so all connections stay busy
until the very end (the trick that makes IDM-style downloaders fast).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

CHUNK_SIZE = 64 * 1024
MIN_SEGMENT_SIZE = 256 * 1024     # don't plan initial segments smaller than this
MIN_SPLIT_SIZE = 1024 * 1024      # only steal work from segments with >= 2x this left
MAX_RETRIES = 5
TIMEOUT = (10, 30)                # (connect, read) seconds
STATE_SAVE_INTERVAL = 2.0

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PyDM/0.1",
    # Ranges refer to the encoded body, so ask for the raw bytes.
    "Accept-Encoding": "identity",
}


class Status:
    QUEUED = "Queued"
    PROBING = "Connecting"
    DOWNLOADING = "Downloading"
    MERGING = "Merging"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    ERROR = "Error"
    CANCELLED = "Cancelled"

    ACTIVE = (PROBING, DOWNLOADING, MERGING)
    RESUMABLE = (PAUSED, ERROR, QUEUED)


class DownloadError(Exception):
    pass


@dataclass
class Segment:
    start: int
    end: int | None          # inclusive; None means unknown length (read to EOF)
    downloaded: int = 0
    active: bool = False

    @property
    def position(self) -> int:
        return self.start + self.downloaded

    @property
    def remaining(self) -> int | None:
        return None if self.end is None else self.end - self.position + 1

    @property
    def done(self) -> bool:
        return self.end is not None and self.position > self.end

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "downloaded": self.downloaded}


# --- helpers -----------------------------------------------------------------

def probe_size(url: str, headers: dict | None = None) -> int | None:
    """Size of the file behind a link (None if the server doesn't say)."""
    with make_session(url, {**DEFAULT_HEADERS, **(headers or {})}) as session:
        r = session.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=TIMEOUT)
        r.close()
        r.raise_for_status()
        m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
        if m:
            return int(m.group(1))
        cl = r.headers.get("Content-Length", "")
        return int(cl) if r.status_code == 200 and cl.isdigit() else None


class _Retire(Exception):
    """A worker leaving because the number of connections was lowered."""


class RateLimiter:
    """Caps the combined speed of all downloads (a token bucket shared by every
    worker thread). Each worker reports what it just read and sleeps off any
    excess, so the total stays at the limit however many connections there are."""

    BURST = 0.5  # seconds of credit that can build up while idle

    def __init__(self, rate: int = 0):
        self._lock = threading.Lock()
        self.rate = rate
        self._credit = 0.0
        self._last = time.monotonic()

    def set_rate(self, bytes_per_second: int) -> None:
        with self._lock:
            self.rate = max(0, int(bytes_per_second))
            self._credit, self._last = 0.0, time.monotonic()

    def consume(self, n: int, stop: threading.Event | None = None) -> None:
        with self._lock:
            if self.rate <= 0:
                return
            now = time.monotonic()
            self._credit = min(self.rate * self.BURST, self._credit + (now - self._last) * self.rate) - n
            self._last = now
            wait = -self._credit / self.rate if self._credit < 0 else 0.0
        # Sleep in slices so lifting the limit, pausing or cancelling takes effect quickly.
        deadline = time.monotonic() + wait
        while wait > 0 and self.rate > 0:
            if stop is not None and stop.wait(min(wait, 0.25)):
                return
            if stop is None:
                time.sleep(min(wait, 0.25))
            wait = deadline - time.monotonic()


LIMITER = RateLimiter()  # shared by every task; DownloadManager.set_speed_limit changes it


_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    name = _INVALID_CHARS.sub("_", name).strip(" .")
    return name[:200] or "download"


def filename_from_response(resp: requests.Response, fallback_url: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*\s*=\s*[^']*'[^']*'([^;]+)", cd, re.I)
    if m:
        return sanitize_filename(unquote(m.group(1).strip().strip('"')))
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        return sanitize_filename(m.group(1).strip())
    for url in (resp.url, fallback_url):
        base = unquote(os.path.basename(urlparse(url).path))
        if base:
            return sanitize_filename(base)
    return "download"


def unique_path(path: Path) -> Path:
    """Return path, or 'name (n).ext' if it (or its .part) is already taken."""
    candidate, n = path, 1
    while candidate.exists() or candidate.with_name(candidate.name + ".part").exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def make_session(url: str, headers: dict) -> requests.Session:
    """Session with the given headers, except that a Cookie header becomes cookies
    scoped to the URL's host, so they aren't leaked to other hosts on redirects or CDNs."""
    session = requests.Session()
    headers = dict(headers)
    cookie_header = headers.pop("Cookie", "")
    session.headers.update(headers)
    host = urlparse(url).hostname or ""
    for part in cookie_header.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            session.cookies.set(name, value, domain=host, path="/")
    return session


def _make_sparse(f) -> None:
    """Mark a file sparse on Windows so writing far past the start doesn't
    force NTFS to zero-fill the gap first. Best effort; harmless elsewhere."""
    if os.name != "nt":
        return
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        FSCTL_SET_SPARSE = 0x900C4
        returned = wintypes.DWORD()
        ctypes.windll.kernel32.DeviceIoControl(
            wintypes.HANDLE(msvcrt.get_osfhandle(f.fileno())), FSCTL_SET_SPARSE,
            None, 0, None, 0, ctypes.byref(returned), None)
    except Exception:
        pass


# --- task --------------------------------------------------------------------

class DownloadTask:
    def __init__(self, url: str, dest_dir: str | os.PathLike, connections: int = 8,
                 filename: str | None = None, headers: dict | None = None,
                 task_id: str | None = None):
        self.id = task_id or uuid.uuid4().hex
        self.url = url
        self.dest_dir = Path(dest_dir)
        self.connections = max(1, connections)
        self.filename = sanitize_filename(filename) if filename else None
        self.headers = {**DEFAULT_HEADERS, **(headers or {})}
        self.added_at = time.time()

        self.status = Status.QUEUED
        self.error: str | None = None
        self.total_size: int | None = None
        self.supports_ranges = False
        self.etag: str | None = None
        self.last_modified: str | None = None
        self.segments: list[Segment] = []
        self.speed = 0.0

        self.on_finished = None   # callback(task), called from the task's thread

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cancel_requested = False
        self._thread: threading.Thread | None = None

    # --- paths ---

    @property
    def final_path(self) -> Path | None:
        return self.dest_dir / self.filename if self.filename else None

    @property
    def part_path(self) -> Path:
        return self.final_path.with_name(self.filename + ".part")

    @property
    def state_path(self) -> Path:
        return self.final_path.with_name(self.filename + ".part.json")

    # --- public control ---

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running or self.status == Status.COMPLETED:
            return
        self._stop.clear()
        self._cancel_requested = False
        self.error = None
        self.status = Status.PROBING
        self._thread = threading.Thread(target=self._run, name=f"dl-{self.id[:6]}", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self.is_running:
            self._stop.set()
        elif self.status == Status.QUEUED:
            self.status = Status.PAUSED

    def cancel(self) -> None:
        """Stop and delete partial data."""
        if self.is_running:
            self._cancel_requested = True
            self._stop.set()
        else:
            self._discard_partial()
            if self.status != Status.COMPLETED:
                self.status = Status.CANCELLED

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # --- progress ---

    @property
    def downloaded(self) -> int:
        with self._lock:
            return sum(s.downloaded for s in self.segments)

    def snapshot(self) -> dict:
        """Consistent, thread-safe view for UIs."""
        with self._lock:
            segs = [(s.start, s.end, s.downloaded, s.active) for s in self.segments]
            downloaded = sum(s[2] for s in segs)
        remaining = (self.total_size - downloaded) if self.total_size else None
        eta = remaining / self.speed if remaining and self.speed > 1 else None
        return {
            "id": self.id, "kind": "file", "url": self.url, "filename": self.filename,
            "path": str(self.final_path) if self.final_path else None,
            "status": self.status, "error": self.error,
            "total_size": self.total_size, "size_estimated": False, "downloaded": downloaded,
            "progress": downloaded / self.total_size if self.total_size else None,
            "speed": self.speed if self.status == Status.DOWNLOADING else 0.0,
            "eta": eta, "supports_ranges": self.supports_ranges,
            "segments": segs, "active_connections": sum(1 for s in segs if s[3]),
            "info": None,
        }

    # --- persistence (task list, owned by the manager) ---

    def to_dict(self) -> dict:
        status = Status.PAUSED if self.status in Status.ACTIVE else self.status
        return {
            "id": self.id, "url": self.url, "dest_dir": str(self.dest_dir),
            "filename": self.filename, "connections": self.connections,
            "headers": self.headers, "added_at": self.added_at, "status": status,
            "error": self.error, "total_size": self.total_size,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DownloadTask":
        t = cls(d["url"], d["dest_dir"], d.get("connections", 8), d.get("filename"),
                d.get("headers"), d["id"])
        t.added_at = d.get("added_at", t.added_at)
        t.status = d.get("status", Status.PAUSED)
        t.error = d.get("error")
        t.total_size = d.get("total_size")
        if t.status == Status.QUEUED:
            t.status = Status.PAUSED
        if t.filename and t.status != Status.COMPLETED:
            t._load_state()  # so progress shows before resuming
        return t

    # --- sidecar state (segment progress) ---

    def set_url(self, url: str, headers: dict | None = None) -> None:
        """Point a stopped download at a fresh link to the same file. Links from
        file hosts and video sites expire; the partial data is kept. On resume the
        size must still match, but the ETag may differ (another server, same file)."""
        if self.is_running:
            raise RuntimeError("Pause the download before changing its link")
        self.url = url
        if headers is not None:
            self.headers = {**self.headers, **headers}
        self.error = None
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            data.update(url=url, etag=None)
            self.state_path.write_text(json.dumps(data), encoding="utf-8")
        except (OSError, ValueError):
            pass
        self.etag = None

    def _save_state(self) -> None:
        if not self.filename or not self.part_path.exists():
            return
        with self._lock:
            data = {
                "url": self.url, "total_size": self.total_size,
                "supports_ranges": self.supports_ranges, "etag": self.etag,
                "last_modified": self.last_modified,
                "segments": [s.to_dict() for s in self.segments],
            }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _load_state(self) -> dict | None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not self.part_path.exists():
            return None
        self.segments = [Segment(s["start"], s["end"], s["downloaded"]) for s in data["segments"]]
        self.total_size = data.get("total_size")
        return data

    def _discard_partial(self) -> None:
        if not self.filename:
            return
        for p in (self.part_path, self.state_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        with self._lock:
            self.segments = []

    # --- download pipeline ---

    def _run(self) -> None:
        try:
            self._prepare()
            if self._stop.is_set():
                return self._stopped()
            self.status = Status.DOWNLOADING
            self._download_all()
            if self.error:
                self.status = Status.ERROR
                self._save_state()
            elif self._stop.is_set():
                self._stopped()
            else:
                self._finalize()
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI
            self.error = str(e) or e.__class__.__name__
            self.status = Status.ERROR
            try:
                self._save_state()
            except OSError:
                pass
        finally:
            self.speed = 0.0
            if self.on_finished:
                self.on_finished(self)

    def _stopped(self) -> None:
        if self._cancel_requested:
            self._discard_partial()
            self.status = Status.CANCELLED
        else:
            self._save_state()
            self.status = Status.PAUSED

    def _probe(self, session: requests.Session) -> requests.Response:
        """Ask for byte 0 only: a 206 reply proves ranges work and reveals the size.
        (More reliable than HEAD, which many servers implement badly.)"""
        r = session.get(self.url, headers={"Range": "bytes=0-0"},
                        stream=True, timeout=TIMEOUT, allow_redirects=True)
        r.close()
        r.raise_for_status()
        if r.status_code == 206:
            m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
            self.total_size = int(m.group(1)) if m else None
            self.supports_ranges = self.total_size is not None
        else:
            cl = r.headers.get("Content-Length", "")
            self.total_size = int(cl) if cl.isdigit() else None
            self.supports_ranges = False
        return r

    def _prepare(self) -> None:
        with make_session(self.url, self.headers) as session:
            resp = self._probe(session)
        etag, last_modified = resp.headers.get("ETag"), resp.headers.get("Last-Modified")

        if not self.filename:
            self.dest_dir.mkdir(parents=True, exist_ok=True)
            self.filename = unique_path(self.dest_dir / filename_from_response(resp, self.url)).name

        saved = self._load_state()
        resumable = (
            saved is not None and self.supports_ranges
            and saved.get("supports_ranges")
            and saved.get("total_size") == self.total_size
            and (not etag or not saved.get("etag") or saved["etag"] == etag)
        )
        self.etag, self.last_modified = etag, last_modified
        if resumable:
            for s in self.segments:
                s.active = False
            return

        # Fresh start (or the remote file changed / can't be resumed).
        self._discard_partial()
        self.segments = self._plan_segments()
        self.dest_dir.mkdir(parents=True, exist_ok=True)
        with open(self.part_path, "wb") as f:
            if self.total_size:
                _make_sparse(f)
                f.truncate(self.total_size)
        self._save_state()

    def _plan_segments(self) -> list[Segment]:
        if self.total_size == 0:
            return []
        if not self.supports_ranges:
            return [Segment(0, self.total_size - 1 if self.total_size else None)]
        count = max(1, min(self.connections, self.total_size // MIN_SEGMENT_SIZE))
        size = self.total_size // count
        bounds = [i * size for i in range(count)] + [self.total_size]
        return [Segment(bounds[i], bounds[i + 1] - 1) for i in range(count)]

    def set_connections(self, n: int) -> None:
        """Change the number of connections, also while downloading: extra workers
        start and steal work; surplus ones finish their current chunk and leave,
        handing their unfinished range back."""
        self.connections = max(1, min(32, int(n)))

    def _target_workers(self) -> int:
        return self.connections if self.supports_ranges else 1

    def _has_work(self) -> bool:
        with self._lock:
            return any(not s.done and not s.active for s in self.segments) or any(
                s.active and not s.done and s.remaining >= 2 * MIN_SPLIT_SIZE for s in self.segments)

    def _download_all(self) -> None:
        self._workers_live = 0
        workers: list[threading.Thread] = []

        def spawn(count: int) -> None:
            for _ in range(count):
                w = threading.Thread(target=self._worker, daemon=True)
                with self._lock:
                    self._workers_live += 1
                w.start()
                workers.append(w)

        spawn(self._target_workers())
        last_bytes, last_time, last_save = self.downloaded, time.monotonic(), time.monotonic()
        while any(w.is_alive() for w in workers):
            time.sleep(0.5)
            missing = self._target_workers() - self._workers_live
            if missing > 0 and not self._stop.is_set() and self._has_work():
                spawn(missing)  # connections were raised
            now, current = time.monotonic(), self.downloaded
            instant = (current - last_bytes) / (now - last_time)
            self.speed = instant if self.speed == 0 else 0.3 * instant + 0.7 * self.speed
            last_bytes, last_time = current, now
            if now - last_save >= STATE_SAVE_INTERVAL:
                self._save_state()
                last_save = now

    def _worker(self) -> None:
        retired = False
        try:
            with make_session(self.url, self.headers) as session:
                while not self._stop.is_set():
                    seg = self._claim_segment()
                    if seg is None:
                        return
                    try:
                        self._fetch_segment(session, seg)
                    except _Retire:
                        retired = True
                        return
                    except Exception as e:  # noqa: BLE001
                        self.error = f"{e.__class__.__name__}: {e}"
                        self._stop.set()
                    finally:
                        with self._lock:
                            seg.active = False  # unfinished ranges go back to the pool
        finally:
            if not retired:
                with self._lock:
                    self._workers_live -= 1

    def _maybe_retire(self) -> None:
        """Called between chunks: leave if there are more workers than connections."""
        if self._workers_live > self._target_workers():
            with self._lock:
                if self._workers_live > self._target_workers():
                    self._workers_live -= 1
                    raise _Retire()

    def _claim_segment(self) -> Segment | None:
        with self._lock:
            for seg in self.segments:
                if not seg.done and not seg.active:
                    seg.active = True
                    return seg
            if not self.supports_ranges:
                return None
            # Work stealing: split the busiest segment and take its back half.
            victim = max((s for s in self.segments if s.active and not s.done),
                         key=lambda s: s.remaining, default=None)
            if victim is None or victim.remaining < 2 * MIN_SPLIT_SIZE:
                return None
            mid = victim.position + victim.remaining // 2
            new = Segment(mid, victim.end, active=True)
            victim.end = mid - 1
            self.segments.insert(self.segments.index(victim) + 1, new)
            return new

    def _fetch_segment(self, session: requests.Session, seg: Segment) -> None:
        failures = 0
        while not seg.done and not self._stop.is_set():
            before = seg.downloaded
            try:
                self._fetch_once(session, seg)
            except (requests.RequestException, OSError) as e:
                if isinstance(e, requests.HTTPError) and e.response is not None \
                        and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    raise  # 403/404/410 won't fix themselves
                failures += 1
                if failures > MAX_RETRIES:
                    raise
                self._stop.wait(min(2 ** failures, 30))
                continue
            if seg.end is None:
                return  # unknown-length stream reached EOF
            if seg.downloaded == before:
                failures += 1  # connection closed without sending anything
                if failures > MAX_RETRIES:
                    raise DownloadError("Server keeps closing the connection")
                self._stop.wait(min(2 ** failures, 30))
            else:
                failures = 0

    def _fetch_once(self, session: requests.Session, seg: Segment) -> None:
        headers = {}
        if self.supports_ranges:
            headers["Range"] = f"bytes={seg.position}-{'' if seg.end is None else seg.end}"
        elif seg.downloaded:
            with self._lock:
                seg.downloaded = 0  # server can't resume; start this one over

        with session.get(self.url, headers=headers, stream=True, timeout=TIMEOUT) as r:
            r.raise_for_status()
            if self.supports_ranges and r.status_code != 206:
                raise DownloadError(f"Server ignored the Range request (HTTP {r.status_code})")
            with open(self.part_path, "r+b") as f:
                f.seek(seg.position)
                for chunk in r.iter_content(CHUNK_SIZE):
                    if self._stop.is_set():
                        return
                    with self._lock:
                        # seg.end may have shrunk because another worker stole the tail.
                        if seg.end is not None:
                            allowed = seg.end - seg.position + 1
                            if allowed <= 0:
                                return
                            chunk = chunk[:allowed]
                    f.write(chunk)
                    with self._lock:
                        seg.downloaded += len(chunk)
                    LIMITER.consume(len(chunk), self._stop)
                    self._maybe_retire()

        if seg.end is None:  # length was unknown: now we know it
            with self._lock:
                seg.end = seg.position - 1
                self.total_size = seg.position

    def _finalize(self) -> None:
        part, state = self.part_path, self.state_path
        if self.total_size == 0 and not part.exists():
            part.touch()
        target = self.final_path
        if target.exists():  # someone created it while we were downloading
            target = unique_path(target)
        os.replace(part, target)
        self.filename = target.name
        state.unlink(missing_ok=True)
        self.status = Status.COMPLETED


# --- manager -----------------------------------------------------------------

class DownloadManager:
    """Owns the task list, persists it, and limits simultaneous downloads."""

    def __init__(self, state_file: str | os.PathLike, max_active: int = 3):
        self.state_file = Path(state_file)
        self.max_active = max_active
        self.tasks: list[DownloadTask] = []
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._load()

    def add(self, url: str, dest_dir, connections: int = 8, filename: str | None = None,
            headers: dict | None = None, start: bool = True, kind: str = "file",
            variant: str | None = None) -> DownloadTask:
        """kind is "file", "hls"/"dash" for a video stream, or "ytdl" for YouTube
        (variant picks the quality)."""
        if kind in ("hls", "dash"):
            from .streams import StreamTask  # streams imports this module
            task = StreamTask(url, dest_dir, connections, filename, headers, kind=kind, variant=variant)
        elif kind == "ytdl":
            from .ytdl import YtdlTask
            task = YtdlTask(url, dest_dir, connections, filename, headers, variant=variant)
        else:
            task = DownloadTask(url, dest_dir, connections, filename, headers)
        if not start:
            task.status = Status.PAUSED
        self._attach(task)
        with self._lock:
            self.tasks.append(task)
        self.save()
        self.pump()
        return task

    def resume(self, task: DownloadTask) -> None:
        if task.status in Status.RESUMABLE + (Status.CANCELLED,) and not task.is_running:
            task.status = Status.QUEUED
            self.pump()

    def pause(self, task: DownloadTask) -> None:
        task.pause()
        self.save()

    def remove(self, task: DownloadTask, delete_files: bool = False) -> None:
        if task.status != Status.COMPLETED:
            task.cancel()
            task.join(5)
        elif delete_files and task.final_path and task.final_path.exists():
            task.final_path.unlink()
        with self._lock:
            if task in self.tasks:
                self.tasks.remove(task)
        self.save()
        self.pump()

    def set_connections(self, task, n: int) -> None:
        task.set_connections(n)
        self.save()

    @staticmethod
    def set_speed_limit(bytes_per_second: int) -> None:
        """0 means unlimited. Applies to running downloads immediately."""
        LIMITER.set_rate(bytes_per_second)

    def get(self, task_id: str) -> DownloadTask | None:
        return next((t for t in self.tasks if t.id == task_id), None)

    def shutdown(self) -> None:
        with self._lock:
            tasks = list(self.tasks)
        for t in tasks:
            t.on_finished = None
            t.pause()
        for t in tasks:
            t.join(10)
        self.save()

    def save(self) -> None:
        with self._lock:
            data = [t.to_dict() for t in self.tasks]
        # Tasks finishing on worker threads save too, so writes must not overlap.
        with self._save_lock:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            for attempt in range(5):
                try:
                    os.replace(tmp, self.state_file)
                    return
                except PermissionError:  # antivirus/indexer briefly holding the file
                    if attempt == 4:
                        raise
                    time.sleep(0.1 * (attempt + 1))

    def _load(self) -> None:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        from .streams import StreamTask
        from .ytdl import YtdlTask
        for d in data:
            kind = d.get("kind")
            cls = StreamTask if kind in ("hls", "dash") else YtdlTask if kind == "ytdl" else DownloadTask
            task = cls.from_dict(d)
            self._attach(task)
            self.tasks.append(task)

    def _attach(self, task: DownloadTask) -> None:
        task.on_finished = self._task_finished

    def _task_finished(self, task: DownloadTask) -> None:
        self.save()
        self.pump()

    def pump(self) -> None:
        """Start queued tasks while there are free slots."""
        with self._lock:
            # Count by status, not thread liveness: this runs from a finishing
            # task's own thread, which is still alive but no longer active.
            running = sum(1 for t in self.tasks if t.status in Status.ACTIVE)
            for t in self.tasks:
                if running >= self.max_active:
                    break
                if t.status == Status.QUEUED and not t.is_running:
                    t.start()
                    running += 1
