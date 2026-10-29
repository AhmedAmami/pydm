"""Video-site downloads through yt-dlp: YouTube, Vimeo, X, Facebook, Reddit,
Twitch and the ~1,800 other sites yt-dlp knows.

YouTube's player (and many others) doesn't fetch plain HLS/DASH playlists, so
the stream code in streams.py can't see their videos. yt-dlp understands the
sites and picks the formats; this module wraps it in the same task interface as
everything else.

Partial files live in "<name>.pydm-parts" like other videos. yt-dlp continues
.part files itself, so resume works after a pause or a crash. Formats are
re-extracted on every start because YouTube's media URLs expire after hours.

Pausing works by raising from yt-dlp's progress hook, the only place it lets us
interrupt a download.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from .engine import LIMITER, DownloadError, Status
from .streams import DrmError, LiveStreamError, StreamTask, Variant, find_ffmpeg

YOUTUBE_HOSTS = re.compile(r"(^|\.)(youtube\.com|youtu\.be|youtube-nocookie\.com)$", re.I)
# Prefer H.264 + AAC at a given resolution: plays everywhere, fits in .mp4.
FORMAT_SORT = ["res", "fps", "vcodec:h264", "acodec:aac"]


class _Paused(Exception):
    pass


def is_youtube(url: str) -> bool:
    try:
        return bool(YOUTUBE_HOSTS.search(urlparse(url).hostname or ""))
    except ValueError:
        return False


# yt-dlp recognises these, but they're file hosts: plain downloads work better.
NOT_VIDEO_SITES = {"Generic", "Dropbox", "GoogleDrive", "GoogleDriveFolder", "Mediafire"}
# A link ending in one of these is a file, whatever site it's on.
FILE_EXTENSIONS = {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso", "img", "dmg", "exe", "msi", "apk", "deb",
                   "rpm", "appimage", "pdf", "epub", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv",
                   "bin", "torrent", "jpg", "jpeg", "png", "gif", "webp", "mp3", "flac", "wav", "m4a", "ogg",
                   "mp4", "mkv", "webm", "avi", "mov", "m3u8", "mpd"}


@lru_cache(maxsize=1)
def _extractors() -> list:
    from yt_dlp.extractor import gen_extractor_classes  # lazy-loaded, ~0.3 s the first time
    return [ie for ie in gen_extractor_classes() if ie.ie_key() not in NOT_VIDEO_SITES]


@lru_cache(maxsize=256)
def site_for(url: str) -> str | None:
    """yt-dlp's name for the video site this page is on (e.g. "Vimeo"), or None if
    yt-dlp doesn't know the site, or the link is a plain file."""
    if is_youtube(url):
        return "YouTube"
    try:
        path = urlparse(url).path.lower()
    except ValueError:
        return None
    if "." in path.rsplit("/", 1)[-1] and path.rsplit(".", 1)[-1] in FILE_EXTENSIONS:
        return None
    try:
        for ie in _extractors():
            if ie.suitable(url):
                return ie.ie_key()
    except Exception:  # noqa: BLE001 - yt-dlp missing or broken: treat as a plain link
        return None
    return None


@lru_cache(maxsize=1)
def _js_runtime() -> dict:
    """yt-dlp needs a JavaScript runtime to decode YouTube's player; the deno
    package ships one. Without it some formats go missing, but it still works."""
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "deno.exe"  # the installed app carries its own
    if getattr(sys, "frozen", False) and bundled.exists():
        return {"deno": {"path": str(bundled)}}
    try:
        import deno
        return {"deno": {"path": deno.find_deno_bin()}}
    except Exception:  # noqa: BLE001 - not installed: let yt-dlp look on PATH
        return {}


def _cookie_file(path: Path, url: str, cookie_header: str) -> None:
    """Write a browser's Cookie header as a Netscape cookie file, scoped to the site."""
    host = (urlparse(url).hostname or "").lower()
    domain = "." + (host[4:] if host.startswith("www.") else host)
    lines = ["# Netscape HTTP Cookie File"]
    for pair in cookie_header.split(";"):
        name, sep, value = pair.strip().partition("=")
        if sep and name:
            lines.append("\t".join([domain, "TRUE", "/", "TRUE", "0", name, value]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class _Logger:
    """yt-dlp prints to the console by default; the GUI has none (pythonw)."""

    def debug(self, msg):
        pass

    info = warning = debug

    def error(self, msg):
        pass  # surfaced via the DownloadError it raises


def _options(task_headers: dict, extra: dict | None = None) -> dict:
    headers = {k: v for k, v in task_headers.items() if k.lower() in ("user-agent", "referer")}
    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "logger": _Logger(),
        "http_headers": headers, "format_sort": FORMAT_SORT, "merge_output_format": "mp4/mkv",
        "js_runtimes": _js_runtime(),
    }
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    return {**opts, **(extra or {})}


def _height_label(f: dict) -> str:
    fps = f.get("fps") or 0
    return f"{f['height']}p" + (f"{int(fps)}" if fps > 30 else "")


MAX_POST_ITEMS = 20  # posts with several videos (Instagram carousels, Facebook albums)


def _entries(info: dict) -> list | None:
    """The videos of a multi-video post, or None for a single video. Real playlists
    (YouTube playlists, channels) are refused: open one video instead."""
    if info.get("_type") not in ("playlist", "multi_video"):
        return None
    entries = list(info.get("entries") or [])
    if is_youtube(info.get("webpage_url") or info.get("original_url") or "") or len(entries) > MAX_POST_ITEMS:
        raise DownloadError("This is a playlist. Open a single video and download that.")
    if not entries:
        raise DownloadError("No video found in this post.")
    return entries


@contextmanager
def _cookies(url: str, headers: dict):
    """The browser's cookies for the site as a temporary cookie file (or None)."""
    if not headers.get("Cookie"):
        yield None
        return
    fd, name = tempfile.mkstemp(prefix="pydm-cookies-", suffix=".txt")
    os.close(fd)
    path = Path(name)
    try:
        _cookie_file(path, url, headers["Cookie"])
        yield path
    finally:
        path.unlink(missing_ok=True)


def _check(info: dict) -> None:
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise LiveStreamError()
    formats = info.get("formats") or []
    if formats and all(f.get("has_drm") for f in formats):
        raise DrmError()


def _extract(url: str, headers: dict) -> dict:
    import yt_dlp  # slow to import; only load it when needed
    try:
        with _cookies(url, headers) as cookiefile, \
                yt_dlp.YoutubeDL(_options(headers, {"cookiefile": str(cookiefile)} if cookiefile else {})) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
            _check(info)
            return ydl.sanitize_info(info)
    except yt_dlp.utils.DownloadError as e:
        raise DownloadError(_clean(e)) from None


def _clean(e: Exception) -> str:
    msg = re.sub(r"^ERROR:\s*(\[\w+\]\s*[\w-]+:\s*)?", "", str(e)).strip()
    # yt-dlp's advice is for its command line (--cookies, its wiki); in PyDM the
    # login comes from the browser, so drop those sentences and say that instead.
    sentences = re.split(r"(?<=[.!?])\s+", msg)
    msg = " ".join(s for s in sentences if "--" not in s and "yt-dlp/wiki" not in s).strip() or msg
    if msg.startswith("Unsupported URL"):
        return (f"This link isn't a video page PyDM can download ({msg.split(':', 1)[-1].strip()}). "
                "Open the video's own post or page (click its date or time), then try again.")
    if re.search(r"Sign in to confirm|logged.in|log in|login|cookies|account|authenticat|private|"
                 r"empty media|not available|rate.limit", str(e), re.I):
        msg = (sentences[0] + " This usually means the site wants you signed in. Sign in to it in your "
               "browser, then send the video to PyDM with the browser extension (the button on the video, "
               "or right-click). Your login comes along with it.")
    return msg or e.__class__.__name__


def list_variants(url: str, headers: dict | None = None) -> tuple[str, list[Variant]]:
    """Qualities by resolution, best first, plus audio only if the site has a
    separate audio track. A post with several videos lists them instead."""
    info = _extract(url, headers or {})
    entries = _entries(info)
    if entries is not None:
        if len(entries) > 1:
            return "ytdl", [Variant(f"item:{i}", f"Video {i} of {len(entries)}" + (
                f" — {e.get('title')}" if isinstance(e, dict) and e.get("title") else ""))
                for i, e in enumerate(entries, 1)]
        info = entries[0] if isinstance(entries[0], dict) and entries[0].get("formats") else info
    formats = [f for f in info.get("formats") or [] if not f.get("has_drm")]
    best: dict[int, dict] = {}
    unnamed = []  # formats that don't say their height (Facebook's "hd"/"sd", ...)
    for f in formats:
        if f.get("vcodec") == "none":
            continue  # audio only
        if f.get("height"):
            if f["height"] not in best or (f.get("fps") or 0) > (best[f["height"]].get("fps") or 0):
                best[f["height"]] = f
        elif f.get("format_id"):
            unnamed.append(f)
    variants = [Variant(str(h), _height_label(f), height=h) for h, f in sorted(best.items(), reverse=True)]
    if unnamed and not variants:
        variants = [Variant("best", "Best quality")] + [
            Variant(f"fmt:{f['format_id']}", (f.get("format_note") or f["format_id"]).upper()) for f in unnamed]
    if any(f.get("vcodec") == "none" and f.get("acodec") not in (None, "none") for f in formats):
        variants.append(Variant("audio", "Audio only (m4a)"))
    return "ytdl", variants


def _format_for(variant: str | None) -> str:
    if variant == "audio":
        return "ba[ext=m4a]/ba"
    if variant and variant.startswith("fmt:"):
        return f"{variant[4:]}/bv*+ba/b"
    if variant and variant.isdigit():
        return f"bv*[height<={variant}]+ba/b[height<={variant}]/bv*+ba/b"
    return "bv*+ba/b"


class YtdlTask(StreamTask):
    """A YouTube video downloaded by yt-dlp. Reuses StreamTask's controls,
    naming and persistence; only the download pipeline differs."""

    def __init__(self, url, dest_dir, connections=8, filename=None, headers=None, task_id=None,
                 kind="ytdl", variant=None):
        super().__init__(url, dest_dir, connections, filename, headers, task_id, "ytdl", variant)
        self._named = bool(filename)   # otherwise the stem comes from the video title
        if not filename:
            self.stem = f"{site_for(url) or 'Online'} video"  # until the real title is known
        self._progress: dict[str, list] = {}   # format id -> [downloaded, total]

    @property
    def downloaded(self) -> int:
        with self._lock:
            return sum(done for done, _ in self._progress.values())

    def snapshot(self) -> dict:
        with self._lock:
            progress = [tuple(v) for v in self._progress.values()]
        downloaded = sum(d for d, _ in progress)
        total = sum(t for _, t in progress) or None
        completed = self.status == Status.COMPLETED
        if completed:
            downloaded = total = self.total_size
        speed = self.speed if self.status == Status.DOWNLOADING else 0.0
        segs, offset = [], 0
        for i, (done, size) in enumerate(progress):  # one bar section per stream (video, audio)
            segs.append((offset, offset + size - 1, done, self.status == Status.DOWNLOADING and done < size))
            offset += size
        return {
            "id": self.id, "kind": self.kind, "url": self.url,
            "filename": self.filename or self.stem,
            "path": str(self.final_path) if self.final_path else None,
            "status": self.status, "error": self.error,
            "total_size": total, "size_estimated": not completed and total is not None,
            "downloaded": downloaded or 0,
            "progress": 1.0 if completed else (downloaded / total if total else None),
            "speed": speed, "eta": (total - downloaded) / speed if total and speed > 1 else None,
            "supports_ranges": True, "segments": segs,
            "active_connections": sum(1 for s in segs if s[3]),
            "info": " · ".join(x for x in (site_for(self.url) or "Video", self.quality) if x),
        }

    def to_dict(self) -> dict:
        return {**super().to_dict(), "named": self._named}

    @classmethod
    def from_dict(cls, d: dict) -> "YtdlTask":
        t = super().from_dict(d)
        t._named = d.get("named", True)
        return t

    def set_url(self, url: str, headers: dict | None = None) -> bool:
        """yt-dlp re-reads the page on every start, so only the page link and the
        cookies change; partial files are kept."""
        if self.is_running:
            raise RuntimeError("Pause the download before changing its link")
        if headers is not None:
            self.headers = {**self.headers, **headers}
        self.url, self.error = url, None
        return True

    def _save_plan(self) -> None:
        data = {"url": self.url, "kind": self.kind, "variant": self.variant, "filename": self.filename}
        (self.parts_dir / "plan.json").write_text(json.dumps(data), encoding="utf-8")

    def _load_plan(self) -> bool:
        return False  # nothing to load: formats are re-extracted on every start

    def _scan_done(self) -> None:
        pass

    def _discard_partial(self) -> None:
        shutil.rmtree(self.parts_dir, ignore_errors=True)
        with self._lock:
            self._progress = {}

    def _run(self) -> None:
        import yt_dlp
        cookiefile = None
        try:
            if self.headers.get("Cookie"):
                fd, name = tempfile.mkstemp(prefix="pydm-cookies-", suffix=".txt")
                os.close(fd)
                cookiefile = Path(name)
                _cookie_file(cookiefile, self.url, self.headers["Cookie"])
            opts = _options(self.headers, {
                "format": _format_for(self.variant),
                "concurrent_fragment_downloads": self.connections,
                "progress_hooks": [self._hook],
                "postprocessor_hooks": [self._pp_hook],
                "retries": 10, "fragment_retries": 10,
                **({"cookiefile": str(cookiefile)} if cookiefile else {}),
            })
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(self.url, download=False, process=False)
                entries = _entries(info)
                item = int(self.variant[5:]) if (self.variant or "").startswith("item:") else 1
                if entries is not None:  # a post with several videos: the chosen one
                    ydl.params["playlist_items"] = str(item)
                    ydl.params["noplaylist"] = False
                _check(info)
                if not self._named:
                    title = info.get("title") or info.get("id") or "video"
                    if entries is not None and len(entries) > 1:
                        title += f" ({item} of {len(entries)})"
                    self.stem = _safe_stem(title)
                    self._named = True
                if self._stop.is_set():
                    return self._stopped()
                chosen = ydl.process_ie_result(info, download=False)
                if entries is not None:
                    picked = [e for e in chosen.get("entries") or [] if e]
                    if not picked:
                        raise DownloadError(f"Video {item} of this post couldn't be read.")
                    chosen = picked[0]
                ext = "." + chosen.get("ext", "mp4")
                if not (self.filename == self.stem + ext and self.parts_dir.is_dir()):
                    self._reserve_name(ext)  # else: resuming into our own folder
                self._save_plan()
                ydl.params["paths"] = {"home": str(self.dest_dir), "temp": str(self.parts_dir)}
                ydl.params["outtmpl"] = {"default": self.stem + ".%(ext)s"}
                formats = chosen.get("requested_formats") or [chosen]
                self.quality = " + ".join(_describe(f) for f in formats)
                with self._lock:
                    self._progress = {f["format_id"]: [0, f.get("filesize") or f.get("filesize_approx") or 0]
                                      for f in formats}
                self.status = Status.DOWNLOADING
                ydl.process_info(chosen)
            if self._stop.is_set():
                return self._stopped()
            self.filename = self._find_output(ext)
            shutil.rmtree(self.parts_dir, ignore_errors=True)
            self.total_size = self.final_path.stat().st_size
            self.status = Status.COMPLETED
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI
            if self._stop.is_set():  # _Paused, possibly wrapped by yt-dlp
                self._stopped()
            elif isinstance(e, yt_dlp.utils.DownloadError):
                self.error, self.status = _clean(e), Status.ERROR
            else:
                self.error, self.status = (str(e) or e.__class__.__name__), Status.ERROR
        finally:
            if cookiefile:
                cookiefile.unlink(missing_ok=True)
            self.speed = 0.0
            if self.on_finished:
                self.on_finished(self)

    def _find_output(self, ext: str) -> str:
        # yt-dlp falls back to .mkv when the streams don't fit in .mp4.
        for candidate in dict.fromkeys([ext, ".mp4", ".mkv", ".webm", ".m4a"]):
            if (self.dest_dir / (self.stem + candidate)).exists():
                return self.stem + candidate
        raise DownloadError("yt-dlp finished but the output file is missing")

    def _hook(self, d: dict) -> None:
        if self._stop.is_set():
            raise _Paused()
        fid = (d.get("info_dict") or {}).get("format_id")
        if not fid:
            return
        new_bytes = 0
        with self._lock:
            entry = self._progress.setdefault(fid, [0, 0])
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or entry[1]
            if d.get("status") == "finished":  # also sent, without byte counts, for files already done
                done = d.get("downloaded_bytes") or d.get("total_bytes") or entry[1]
                entry[:] = [done, done]
            else:
                new_bytes = max(0, (d.get("downloaded_bytes") or 0) - entry[0])
                entry[0] = d.get("downloaded_bytes") or 0
                entry[1] = max(int(total), entry[0])
        # The hook runs on yt-dlp's download thread, so sleeping here throttles it.
        LIMITER.consume(new_bytes, self._stop)
        if d.get("speed"):
            self.speed = d["speed"] if self.speed == 0 else 0.3 * d["speed"] + 0.7 * self.speed

    def _pp_hook(self, d: dict) -> None:
        if d.get("status") == "started" and d.get("postprocessor") in ("Merger", "FFmpegMerger"):
            self.status = Status.MERGING


def _safe_stem(title: str) -> str:
    from .engine import sanitize_filename
    return sanitize_filename(title)[:150].rstrip(" .") or "video"


def _describe(f: dict) -> str:
    if f.get("vcodec") == "none":
        return "audio " + (f.get("ext") or "")
    if f.get("height"):
        codec = (f.get("vcodec") or "").split(".")[0]
        return _height_label(f) + {"avc1": " H.264", "vp9": " VP9", "vp09": " VP9", "av01": " AV1"}.get(codec, "")
    return (f.get("format_note") or f.get("format_id") or "video").upper()  # e.g. Facebook's "HD"
