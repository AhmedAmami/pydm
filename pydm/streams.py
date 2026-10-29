"""HLS (.m3u8) and DASH (.mpd) video downloads.

A playlist becomes a list of tracks (video, audio, or both muxed together). Each
track is an ordered list of pieces: init section, media segments, AES keys.
Pieces are fetched in parallel into a "<name>.pydm-parts" folder. A piece is
done once its file exists, which is all resume needs. ffmpeg then joins the
pieces and muxes the tracks into one file.

DRM-protected streams (Widevine, PlayReady, FairPlay, SAMPLE-AES) are detected
and refused. Plain AES-128 HLS encryption is not DRM - the key is served to any
player that asks - and ffmpeg decrypts it like a player would.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests

from .engine import (CHUNK_SIZE, DEFAULT_HEADERS, LIMITER, MAX_RETRIES, TIMEOUT, DownloadError, Status,
                     make_session, sanitize_filename, unique_path)

PARTS_SUFFIX = ".pydm-parts"
MEDIA_EXTENSIONS = {".mp4", ".mkv", ".webm", ".ts", ".m4a", ".aac", ".mov", ".m3u8", ".mpd"}
GENERIC_STEMS = {"master", "index", "playlist", "manifest", "chunklist", "stream", "main",
                 "prog_index", "media", "video", "mono", "stereo"}


class DrmError(DownloadError):
    def __init__(self, scheme: str = ""):
        super().__init__("This video is DRM-protected" + (f" ({scheme})" if scheme else "")
                         + " and can't be downloaded.")


class LiveStreamError(DownloadError):
    def __init__(self):
        super().__init__("This is a live stream. Only on-demand videos can be downloaded.")


@dataclass
class Piece:
    url: str
    file: str                                  # file name inside the parts folder
    byterange: tuple[int, int] | None = None   # inclusive
    key: bool = False                          # an AES key, not media
    init: bool = False                         # init section: tiny, so left out of size estimates


@dataclass
class Track:
    type: str                         # "video", "audio" or "muxed"
    container: str                    # "ts", "mp4", "webm" or "aac"
    pieces: list[Piece] = field(default_factory=list)
    hls_playlist: str | None = None   # local playlist pointing at the piece files (HLS only)
    encrypted: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Track":
        pieces = [Piece(p["url"], p["file"], tuple(p["byterange"]) if p["byterange"] else None, p["key"],
                        p.get("init", False)) for p in d["pieces"]]
        return cls(d["type"], d["container"], pieces, d["hls_playlist"], d["encrypted"])

    @property
    def media(self) -> list[Piece]:
        return [p for p in self.pieces if not p.key]


@dataclass
class Variant:
    id: str
    label: str
    bandwidth: int = 0
    height: int | None = None


@lru_cache(maxsize=1)
def find_ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - not installed, or no binary for this platform
        return None


def guess_kind(url: str, content_type: str = "", body: str = "") -> str | None:
    from .ytdl import is_youtube, site_for
    if is_youtube(url):
        return "ytdl"
    path, ct = urlparse(url).path.lower(), content_type.lower()
    if path.endswith(".m3u8") or "mpegurl" in ct or body.lstrip("﻿ \r\n").startswith("#EXTM3U"):
        return "hls"
    if path.endswith(".mpd") or "dash+xml" in ct or "<MPD" in body[:4096]:
        return "dash"
    if not content_type and not body and site_for(url):  # a video page yt-dlp knows (Vimeo, X, ...)
        return "ytdl"
    return None


def _get_text(session: requests.Session, url: str) -> tuple[str, str, str]:
    """Return (body, final URL after redirects, content type)."""
    r = session.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content.decode("utf-8", "replace").lstrip("﻿"), r.url, r.headers.get("Content-Type", "")


def _drm_name(text: str) -> str:
    text = text.lower()
    for needle, name in (("widevine", "Widevine"), ("edef8ba9", "Widevine"), ("playready", "PlayReady"),
                         ("9a04f079", "PlayReady"), ("streamingkeydelivery", "FairPlay"),
                         ("fairplay", "FairPlay"), ("94ce86fb", "FairPlay")):
        if needle in text:
            return name
    return ""


def _label(height: int | None, bandwidth: int, codecs: str, audio_only: bool = False) -> str:
    parts = [f"{height}p" if height else ("Audio only" if audio_only else "Default")]
    if bandwidth:
        parts.append(f"{bandwidth / 1e6:.1f} Mbps" if bandwidth >= 1e6 else f"{bandwidth // 1000} kbps")
    codec = codecs.split(",")[0].strip().lower()
    for prefix, name in (("avc", "H.264"), ("hvc", "H.265"), ("hev", "H.265"), ("vp09", "VP9"),
                         ("vp9", "VP9"), ("av01", "AV1"), ("mp4a", "AAC"), ("opus", "Opus")):
        if codec.startswith(prefix):
            parts.append(name)
            break
    return " · ".join(parts)


# --- HLS -----------------------------------------------------------------------

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_SEG_EXT = {"ts": "ts", "mp4": "mp4", "aac": "aac", "webm": "webm"}


def _attrs(line: str) -> dict:
    return {k: v.strip('"') for k, v in _ATTR_RE.findall(line.split(":", 1)[1])}


def _hls_key_is_aes(a: dict) -> bool:
    """True for plain AES-128, False for no encryption; raises DrmError for DRM."""
    method = a.get("METHOD", "NONE").upper()
    if method == "NONE":
        return False
    keyformat = a.get("KEYFORMAT", "identity")
    if method == "AES-128" and keyformat.lower() == "identity":
        return True
    raise DrmError(_drm_name(keyformat + a.get("URI", "")) or method)


def _container_from_url(url: str) -> str:
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext == ".aac":
        return "aac"
    if ext in (".mp4", ".m4s", ".m4a", ".m4v", ".cmfv", ".cmfa"):
        return "mp4"
    return "ts"


def _hls_master_variants(text: str, base: str) -> tuple[list[tuple[Variant, dict]], list[dict]]:
    """Variants (best first) with their attributes, plus the #EXT-X-MEDIA renditions."""
    lines = [line.strip() for line in text.splitlines()]
    variants, media = [], []
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-SESSION-KEY:"):
            _hls_key_is_aes(_attrs(line))
        elif line.startswith("#EXT-X-STREAM-INF:"):
            a = _attrs(line)
            uri = next((u for u in lines[i + 1:] if u and not u.startswith("#")), None)
            if not uri:
                continue
            res = a.get("RESOLUTION", "")
            height = int(res.split("x")[1]) if "x" in res else None
            bandwidth = int(a.get("BANDWIDTH") or a.get("AVERAGE-BANDWIDTH") or 0)
            codecs = a.get("CODECS", "")
            audio_only = height is None and bool(codecs) and all(
                c.strip().startswith(("mp4a", "ac-3", "ec-3", "opus")) for c in codecs.split(","))
            variants.append((Variant(urljoin(base, uri), _label(height, bandwidth, codecs, audio_only),
                                     bandwidth, height), a))
        elif line.startswith("#EXT-X-MEDIA:"):
            a = _attrs(line)
            if a.get("URI"):
                a["URI"] = urljoin(base, a["URI"])
            media.append(a)
    variants.sort(key=lambda v: (v[0].height or 0, v[0].bandwidth), reverse=True)
    return variants, media


def _parse_hls_media(text: str, base: str, track_type: str, prefix: str) -> Track:
    if "#EXT-X-ENDLIST" not in text:
        raise LiveStreamError()
    track = Track(track_type, "ts")
    out: list[str] = []
    keys: dict[str, str] = {}
    maps: dict[tuple, str] = {}
    next_offset: dict[str, int] = {}   # implicit byterange offsets continue per URI
    key = None                         # (local key file, IV or None) while encryption is on
    seq, target, duration, pending_range, n = 0, 1, "0", None, 0

    def byterange(spec: str, url: str) -> tuple[int, int]:
        length, _, offset = spec.partition("@")
        start = int(offset) if offset else next_offset.get(url, 0)
        next_offset[url] = start + int(length)
        return start, start + int(length) - 1

    for line in (raw.strip() for raw in text.splitlines()):
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            seq = int(line.split(":", 1)[1])
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            target = math.ceil(float(line.split(":", 1)[1]))
        elif line.startswith("#EXT-X-KEY:"):
            a = _attrs(line)
            if _hls_key_is_aes(a):
                url = urljoin(base, a["URI"])
                if url not in keys:
                    keys[url] = f"{prefix}key{len(keys)}.key"
                    track.pieces.append(Piece(url, keys[url], key=True))
                key = (keys[url], a.get("IV"))
                track.encrypted = True
            else:
                key = None
        elif line.startswith("#EXT-X-MAP:"):
            a = _attrs(line)
            url = urljoin(base, a["URI"])
            br = byterange(a["BYTERANGE"], url) if a.get("BYTERANGE") else None
            if (url, br) not in maps:
                maps[(url, br)] = f"{prefix}init{len(maps)}.mp4"
                track.pieces.append(Piece(url, maps[(url, br)], br, init=True))
            out.append(f'#EXT-X-MAP:URI="{maps[(url, br)]}"')
            track.container = "mp4"
        elif line.startswith("#EXT-X-BYTERANGE:"):
            pending_range = line.split(":", 1)[1]
        elif line.startswith("#EXTINF:"):
            duration = line.split(":", 1)[1].split(",")[0]
        elif line == "#EXT-X-DISCONTINUITY":
            out.append(line)
        elif not line.startswith("#"):
            url = urljoin(base, line)
            br = byterange(pending_range, url) if pending_range else None
            pending_range = None
            if n == 0 and track.container != "mp4":
                track.container = _container_from_url(url)
            name = f"{prefix}seg{n:05d}.{_SEG_EXT[track.container]}"
            track.pieces.append(Piece(url, name, br))
            if track.encrypted:
                # Spell out the key and IV on every segment: we renumber the media
                # sequence, and the default IV is derived from the original number.
                out.append(f'#EXT-X-KEY:METHOD=AES-128,URI="{key[0]}",IV={key[1] or f"0x{seq:032x}"}'
                           if key else "#EXT-X-KEY:METHOD=NONE")
            out += [f"#EXTINF:{duration},", name]
            n += 1
            seq += 1
    if n == 0:
        raise DownloadError("The playlist has no video segments")
    header = ["#EXTM3U", "#EXT-X-VERSION:7", f"#EXT-X-TARGETDURATION:{target}",
              "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD"]
    track.hls_playlist = "\n".join(header + out + ["#EXT-X-ENDLIST", ""])
    return track


def _hls_plan(session, url: str, variant_id: str | None) -> tuple[list[Track], str]:
    text, base, _ = _get_text(session, url)
    if "#EXT-X-STREAM-INF" not in text:
        return [_parse_hls_media(text, base, "muxed", "v_")], ""
    variants, media = _hls_master_variants(text, base)
    if not variants:
        raise DownloadError("The playlist lists no playable variants")
    variant, attrs = next((v for v in variants if v[0].id == variant_id), variants[0])
    group = attrs.get("AUDIO")
    renditions = [m for m in media if m.get("TYPE") == "AUDIO" and m.get("GROUP-ID") == group and m.get("URI")]
    audio = next((m for m in renditions if m.get("DEFAULT") == "YES"), renditions[0] if renditions else None)

    vtext, vbase, _ = _get_text(session, variant.id)
    tracks = [_parse_hls_media(vtext, vbase, "video" if audio else "muxed", "v_")]
    if audio:
        atext, abase, _ = _get_text(session, audio["URI"])
        tracks.append(_parse_hls_media(atext, abase, "audio", "a_"))
    return tracks, variant.label


# --- DASH ----------------------------------------------------------------------

_TEMPLATE_RE = re.compile(r"\$(RepresentationID|Number|Bandwidth|Time)(?:%0(\d+)d)?\$")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(el, name: str) -> list:
    return [c for c in el if _local(c.tag) == name]


def _kid(el, name: str):
    found = _kids(el, name)
    return found[0] if found else None


def _iso_duration(s: str | None) -> float | None:
    m = re.fullmatch(r"P(?:([\d.]+)Y)?(?:([\d.]+)M)?(?:([\d.]+)D)?"
                     r"(?:T(?:([\d.]+)H)?(?:([\d.]+)M)?(?:([\d.]+)S)?)?", (s or "").strip())
    if not s or not m:
        return None
    y, mo, d, h, mi, sec = (float(g) if g else 0.0 for g in m.groups())
    return ((y * 365 + mo * 30 + d) * 24 + h) * 3600 + mi * 60 + sec


def _with_base(el, parent: str) -> str:
    b = _kid(el, "BaseURL")
    return urljoin(parent, b.text.strip()) if b is not None and b.text else parent


def _dash_representations(text: str, url: str) -> tuple[list[dict], float | None]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise DownloadError(f"Invalid DASH manifest: {e}") from e
    if root.get("type") == "dynamic":
        raise LiveStreamError()
    for el in root.iter():
        if _local(el.tag) == "ContentProtection":
            raise DrmError(_drm_name((el.get("schemeIdUri") or "") + (el.get("value") or "")))
    periods = _kids(root, "Period")
    if len(periods) != 1:
        raise DownloadError("DASH manifests with multiple periods aren't supported yet")
    period = periods[0]
    duration = _iso_duration(period.get("duration")) or _iso_duration(root.get("mediaPresentationDuration"))
    period_base = _with_base(period, _with_base(root, url))
    reps = []
    for aset in _kids(period, "AdaptationSet"):
        aset_base = _with_base(aset, period_base)
        for rep in _kids(aset, "Representation"):
            mime = rep.get("mimeType") or aset.get("mimeType") or ""
            kind = aset.get("contentType") or rep.get("contentType") or mime.split("/")[0]
            if kind not in ("video", "audio"):
                continue
            reps.append({
                "id": rep.get("id") or f"rep{len(reps)}", "type": kind, "mime": mime,
                "bandwidth": int(rep.get("bandwidth") or 0),
                "height": int(rep.get("height") or aset.get("height") or 0) or None,
                "codecs": rep.get("codecs") or aset.get("codecs") or "",
                "main": any(r.get("value") == "main" for r in _kids(aset, "Role")),
                "elements": (period, aset, rep), "base": _with_base(rep, aset_base),
            })
    return reps, duration


def _fill_template(template: str, rep: dict, number: int | None = None, time_: int | None = None) -> str:
    values = {"RepresentationID": rep["id"], "Number": number, "Bandwidth": rep["bandwidth"], "Time": time_}

    def sub(m):
        value = str(values[m.group(1)])
        return value.zfill(int(m.group(2))) if m.group(2) else value
    return _TEMPLATE_RE.sub(sub, template).replace("$$", "$")


def _range(spec: str | None) -> tuple[int, int] | None:
    if not spec:
        return None
    start, end = spec.split("-")
    return int(start), int(end)


def _dash_track(rep: dict, duration: float | None, prefix: str) -> Track:
    container = "webm" if "webm" in rep["mime"] else "mp4"
    track = Track(rep["type"], container)
    base = rep["base"]
    templates = [t for el in rep["elements"] for t in _kids(el, "SegmentTemplate")]
    seg_lists = [s for el in rep["elements"] for s in _kids(el, "SegmentList")]

    if templates:
        # Inner levels (Representation) override outer ones (Period, AdaptationSet).
        attrs, timeline = {}, None
        for t in templates:
            attrs.update(t.attrib)
            if _kid(t, "SegmentTimeline") is not None:
                timeline = _kid(t, "SegmentTimeline")
        timescale, number = int(attrs.get("timescale", 1)), int(attrs.get("startNumber", 1))
        if "initialization" in attrs:
            track.pieces.append(Piece(urljoin(base, _fill_template(attrs["initialization"], rep)),
                                      f"{prefix}init.{container}", init=True))
        media = attrs.get("media")
        if not media:
            raise DownloadError("DASH SegmentTemplate has no media attribute")
        urls = []
        if timeline is not None:
            t, entries = 0, _kids(timeline, "S")
            for i, s in enumerate(entries):
                if s.get("t") is not None:
                    t = int(s.get("t"))
                d, r = int(s.get("d")), int(s.get("r", 0))
                if r < 0:  # repeat until the next entry, or the end of the period
                    nxt = entries[i + 1].get("t") if i + 1 < len(entries) else None
                    if nxt is None and duration is None:
                        raise DownloadError("Can't work out the length of this stream")
                    end = int(nxt) if nxt is not None else \
                        int(attrs.get("presentationTimeOffset", 0)) + duration * timescale
                    r = math.ceil((end - t) / d) - 1
                for _ in range(r + 1):
                    urls.append(_fill_template(media, rep, number, t))
                    t += d
                    number += 1
        else:
            if not duration or "duration" not in attrs:
                raise DownloadError("Can't work out how many segments this stream has")
            count = math.ceil(duration * timescale / int(attrs["duration"]))
            urls = [_fill_template(media, rep, number + i) for i in range(count)]
        track.pieces += [Piece(urljoin(base, u), f"{prefix}seg{i:05d}.{container}") for i, u in enumerate(urls)]
    elif seg_lists:
        seg_list = seg_lists[-1]
        init = _kid(seg_list, "Initialization")
        if init is not None:
            track.pieces.append(Piece(urljoin(base, init.get("sourceURL") or ""), f"{prefix}init.{container}",
                                      _range(init.get("range")), init=True))
        for i, su in enumerate(_kids(seg_list, "SegmentURL")):
            track.pieces.append(Piece(urljoin(base, su.get("media") or ""), f"{prefix}seg{i:05d}.{container}",
                                      _range(su.get("mediaRange"))))
    else:
        # SegmentBase, or a bare BaseURL: the representation is one ordinary file.
        track.pieces.append(Piece(base, f"{prefix}full.{container}"))
    if not track.media:
        raise DownloadError("The DASH manifest has no segments")
    return track


def _dash_video_variants(reps: list[dict]) -> list[Variant]:
    videos = sorted((r for r in reps if r["type"] == "video"),
                    key=lambda r: (r["height"] or 0, r["bandwidth"]), reverse=True)
    return [Variant(r["id"], _label(r["height"], r["bandwidth"], r["codecs"]), r["bandwidth"], r["height"])
            for r in videos]


def _dash_plan(session, url: str, variant_id: str | None) -> tuple[list[Track], str]:
    text, final_url, _ = _get_text(session, url)
    reps, duration = _dash_representations(text, final_url)
    by_id = {r["id"]: r for r in reps}
    videos = _dash_video_variants(reps)
    audios = [r for r in reps if r["type"] == "audio"]
    if not videos and not audios:
        raise DownloadError("The DASH manifest has no audio or video")
    tracks, label = [], ""
    if videos:
        chosen = next((v for v in videos if v.id == variant_id), videos[0])
        tracks.append(_dash_track(by_id[chosen.id], duration, "v_"))
        label = chosen.label
    if audios:
        preferred = [r for r in audios if r["main"]] or audios
        tracks.append(_dash_track(max(preferred, key=lambda r: r["bandwidth"]), duration, "a_"))
        if not videos:
            label = "Audio only"
    if len(tracks) == 1 and videos:
        tracks[0].type = "muxed"
    return tracks, label


def list_variants(url: str, headers: dict | None = None, kind: str | None = None) -> tuple[str, list[Variant]]:
    """Fetch a playlist and return (kind, qualities, best first). Raises DrmError /
    LiveStreamError early, so the UI can explain before anything is queued."""
    if kind == "ytdl" or guess_kind(url) == "ytdl":
        from . import ytdl
        return ytdl.list_variants(url, headers)
    with make_session(url, {**DEFAULT_HEADERS, **(headers or {})}) as session:
        text, final_url, content_type = _get_text(session, url)
        kind = guess_kind(final_url, content_type, text) or kind
        if kind == "hls":
            if "#EXT-X-STREAM-INF" not in text:
                _parse_hls_media(text, final_url, "muxed", "")
                return "hls", []
            variants, _ = _hls_master_variants(text, final_url)
            if variants:  # DRM keys usually live in the media playlists
                vtext, vbase, _ = _get_text(session, variants[0][0].id)
                _parse_hls_media(vtext, vbase, "muxed", "")
            return "hls", [v for v, _ in variants]
        if kind == "dash":
            reps, _ = _dash_representations(text, final_url)
            return "dash", _dash_video_variants(reps)
    raise DownloadError("This link isn't an HLS or DASH video playlist")


# --- task ----------------------------------------------------------------------

def _stem_for(filename: str | None, url: str) -> str:
    if filename:
        name = sanitize_filename(filename)
        root, ext = os.path.splitext(name)
        return root if root and ext.lower() in MEDIA_EXTENSIONS else name
    stem = Path(unquote(urlparse(url).path)).stem
    return sanitize_filename(stem) if stem and stem.lower() not in GENERIC_STEMS else "video"


class StreamTask:
    """Same interface as engine.DownloadTask, so the manager and GUI treat both alike."""

    def __init__(self, url: str, dest_dir, connections: int = 8, filename: str | None = None,
                 headers: dict | None = None, task_id: str | None = None, kind: str = "hls",
                 variant: str | None = None):
        self.id = task_id or uuid.uuid4().hex
        self.url = url
        self.kind = kind
        self.variant = variant
        self.dest_dir = Path(dest_dir)
        self.connections = max(1, connections)
        self.headers = {**DEFAULT_HEADERS, **(headers or {})}
        self.added_at = time.time()
        self.stem = _stem_for(filename, url)
        self.filename: str | None = None   # chosen once the output format is known

        self.status = Status.QUEUED
        self.error: str | None = None
        self.supports_ranges = True        # resumable segment by segment
        self.quality = ""
        self.tracks: list[Track] = []
        self.speed = 0.0
        self.total_size: int | None = None  # known once finished
        self.on_finished = None

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._cancel_requested = False
        self._thread: threading.Thread | None = None
        self._done_sizes: dict[str, int] = {}   # finished media file -> bytes
        self._inflight: dict[str, int] = {}

    # --- paths ---

    @property
    def final_path(self) -> Path | None:
        return self.dest_dir / self.filename if self.filename else None

    @property
    def parts_dir(self) -> Path:
        return self.dest_dir / (self.stem + PARTS_SUFFIX)

    # --- control ---

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
        self._thread = threading.Thread(target=self._run, name=f"stream-{self.id[:6]}", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self.is_running:
            self._stop.set()
        elif self.status == Status.QUEUED:
            self.status = Status.PAUSED

    def cancel(self) -> None:
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
            return sum(self._done_sizes.values()) + sum(self._inflight.values())

    def _estimate_size(self, sizes: dict[str, int]) -> int | None:
        """Finished bytes plus, per track, the average segment size times the segments left.
        Init sections are excluded from the average: they're a few hundred bytes."""
        estimate, any_sample = 0, False
        for track in self.tracks:
            segments = [p for p in track.media if not p.init]
            done = [sizes[p.file] for p in segments if p.file in sizes]
            estimate += sum(sizes.get(p.file, 0) for p in track.media)
            if done:
                any_sample = True
                estimate += sum(done) // len(done) * (len(segments) - len(done))
        return estimate if any_sample else None

    def snapshot(self) -> dict:
        media = [p for t in self.tracks for p in t.media]
        with self._lock:
            sizes = dict(self._done_sizes)
            downloaded = sum(sizes.values()) + sum(self._inflight.values())
            segs = [(i, i, 1 if p.file in sizes else 0, p.file in self._inflight)
                    for i, p in enumerate(media)]
        done, total = len(sizes), len(media)
        completed = self.status == Status.COMPLETED
        if completed:
            progress, size, estimated = 1.0, self.total_size, False
        else:
            progress = done / total if total else None
            size = self._estimate_size(sizes)
            estimated = True
        eta = (size - downloaded) / self.speed if size and not completed and self.speed > 1 else None
        info = " · ".join(x for x in (self.kind.upper(), self.quality,
                                      f"{done}/{total} segments" if total and not completed else "",
                                      f"{len(self.tracks)} tracks" if len(self.tracks) > 1 else "") if x)
        return {
            "id": self.id, "kind": self.kind, "url": self.url,
            "filename": self.filename or self.stem,
            "path": str(self.final_path) if self.final_path else None,
            "status": self.status, "error": self.error,
            "total_size": size, "size_estimated": estimated and size is not None,
            "downloaded": downloaded if not completed else (self.total_size or downloaded),
            "progress": progress,
            "speed": self.speed if self.status == Status.DOWNLOADING else 0.0,
            "eta": eta, "supports_ranges": True, "segments": segs,
            "active_connections": sum(1 for s in segs if s[3]), "info": info,
        }

    # --- persistence ---

    def to_dict(self) -> dict:
        status = Status.PAUSED if self.status in Status.ACTIVE else self.status
        return {
            "id": self.id, "kind": self.kind, "url": self.url, "variant": self.variant,
            "dest_dir": str(self.dest_dir), "stem": self.stem, "filename": self.filename,
            "connections": self.connections, "headers": self.headers, "added_at": self.added_at,
            "status": status, "error": self.error, "quality": self.quality, "total_size": self.total_size,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StreamTask":
        t = cls(d["url"], d["dest_dir"], d.get("connections", 8), None, d.get("headers"), d["id"],
                d.get("kind", "hls"), d.get("variant"))
        t.stem = d.get("stem") or t.stem
        t.filename = d.get("filename")
        t.added_at = d.get("added_at", t.added_at)
        t.status = d.get("status", Status.PAUSED)
        t.error = d.get("error")
        t.quality = d.get("quality", "")
        t.total_size = d.get("total_size")
        if t.status == Status.QUEUED:
            t.status = Status.PAUSED
        if t.status != Status.COMPLETED and t._load_plan():
            t._scan_done()  # so progress shows before resuming
        return t

    def set_connections(self, n: int) -> None:
        """Segments fetched at once; takes effect immediately (for yt-dlp videos, at the next start)."""
        self.connections = max(1, min(32, int(n)))

    def set_url(self, url: str, headers: dict | None = None) -> bool:
        """Use a fresh link for this video (signed CDN links expire). Segments
        already downloaded are kept if the new link offers the same quality and the
        same number of segments; otherwise the video starts over. Returns True if
        the downloaded segments were kept."""
        if self.is_running:
            raise RuntimeError("Pause the download before changing its link")
        old_count = sum(len(t.media) for t in self.tracks)
        if headers is not None:
            self.headers = {**self.headers, **headers}
        self.url, self.error = url, None
        if self.variant is not None:
            _, variants = list_variants(url, self.headers, self.kind)
            same = next((v for v in variants if v.label == self.quality), None)
            self.variant = same.id if same else None
        else:
            same = True
        keep = False
        if same and old_count:
            with make_session(url, self.headers) as session:
                planner = _hls_plan if self.kind == "hls" else _dash_plan
                tracks, quality = planner(session, url, self.variant)
            keep = quality == self.quality and sum(len(t.media) for t in tracks) == old_count
            if keep:
                self.tracks = tracks
        if keep:
            self._save_plan()
        else:
            self._discard_partial()   # different video or quality: start over
            self.tracks, self.quality = [], ""
        self._scan_done()
        return keep

    def _save_plan(self) -> None:
        data = {"url": self.url, "kind": self.kind, "variant": self.variant, "quality": self.quality,
                "filename": self.filename, "tracks": [t.to_dict() for t in self.tracks]}
        (self.parts_dir / "plan.json").write_text(json.dumps(data), encoding="utf-8")

    def _load_plan(self) -> bool:
        if not self.filename:
            return False
        try:
            data = json.loads((self.parts_dir / "plan.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        self.tracks = [Track.from_dict(t) for t in data["tracks"]]
        self.quality = data.get("quality", self.quality)
        return True

    def _scan_done(self) -> None:
        sizes = {}
        for p in (p for t in self.tracks for p in t.media):
            f = self.parts_dir / p.file
            if f.exists():
                sizes[p.file] = f.stat().st_size
        with self._lock:
            self._done_sizes, self._inflight = sizes, {}

    def _discard_partial(self) -> None:
        shutil.rmtree(self.parts_dir, ignore_errors=True)
        with self._lock:
            self._done_sizes, self._inflight = {}, {}

    # --- pipeline ---

    def _run(self) -> None:
        try:
            if not self._load_plan():
                self._plan()
            if self._stop.is_set():
                return self._stopped()
            self.status = Status.DOWNLOADING
            self._download_pieces()
            if self.error:
                self.status = Status.ERROR
                return
            if self._stop.is_set():
                return self._stopped()
            self.status = Status.MERGING
            self._merge()
            if self._stop.is_set():
                return self._stopped()
            shutil.rmtree(self.parts_dir, ignore_errors=True)
            self.total_size = self.final_path.stat().st_size
            self.status = Status.COMPLETED
        except Exception as e:  # noqa: BLE001 - surface any failure to the UI
            self.error = str(e) or e.__class__.__name__
            self.status = Status.ERROR
        finally:
            self.speed = 0.0
            if self.on_finished:
                self.on_finished(self)

    def _stopped(self) -> None:
        if self._cancel_requested:
            self._discard_partial()
            self.status = Status.CANCELLED
        else:
            self.status = Status.PAUSED

    def _plan(self) -> None:
        with make_session(self.url, self.headers) as session:
            planner = _hls_plan if self.kind == "hls" else _dash_plan
            self.tracks, self.quality = planner(session, self.url, self.variant)
        single = len(self.tracks) == 1 and not self.tracks[0].encrypted
        if single and not find_ffmpeg():
            ext = "." + self.tracks[0].container  # no ffmpeg: the joined segments as they are
        else:
            ext = ".mkv" if any(t.container == "webm" for t in self.tracks) else ".mp4"
        if self.filename and self.parts_dir.is_dir():
            self.filename = self.stem + ext
        else:
            self._reserve_name(ext)
        self._save_plan()
        self._scan_done()

    def _reserve_name(self, ext: str) -> None:
        """Pick a stem whose output and parts folder are both free. mkdir is atomic,
        so two downloads of the same video can't end up sharing a folder."""
        n, base = 0, self.stem
        while True:
            stem = base if n == 0 else f"{base} ({n})"
            if not (self.dest_dir / (stem + ext)).exists():
                try:
                    (self.dest_dir / (stem + PARTS_SUFFIX)).mkdir(parents=True)
                    break
                except FileExistsError:
                    pass
            n += 1
        self.stem, self.filename = stem, stem + ext

    def _download_pieces(self) -> None:
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        self._scan_done()
        pieces = [p for t in self.tracks for p in t.pieces
                  if not (self.parts_dir / p.file).exists()]
        pieces.sort(key=lambda p: not p.key)  # keys first; they're tiny
        queue_lock, position = threading.Lock(), [0]

        def next_piece() -> Piece | None:
            with queue_lock:
                if position[0] >= len(pieces):
                    return None
                position[0] += 1
                return pieces[position[0] - 1]

        live = [0]  # running workers; connections can change while downloading

        def worker() -> None:
            retired = False
            try:
                with make_session(self.url, self.headers) as session:
                    while not self._stop.is_set():
                        with queue_lock:
                            if live[0] > self.connections:  # connections were lowered
                                live[0] -= 1
                                retired = True
                                return
                        piece = next_piece()
                        if piece is None:
                            return
                        try:
                            self._fetch_piece(session, piece)
                        except Exception as e:  # noqa: BLE001
                            self.error = f"{e.__class__.__name__}: {e}"
                            self._stop.set()
            finally:
                if not retired:
                    with queue_lock:
                        live[0] -= 1

        workers: list[threading.Thread] = []

        def spawn(count: int) -> None:
            for _ in range(count):
                with queue_lock:
                    live[0] += 1
                w = threading.Thread(target=worker, daemon=True)
                w.start()
                workers.append(w)

        spawn(min(self.connections, max(1, len(pieces))))
        last_bytes, last_time = self.downloaded, time.monotonic()
        while any(w.is_alive() for w in workers):
            time.sleep(0.5)
            with queue_lock:
                missing = min(self.connections, len(pieces) - position[0]) - live[0]
            if missing > 0 and not self._stop.is_set():
                spawn(missing)  # connections were raised
            now, current = time.monotonic(), self.downloaded
            instant = (current - last_bytes) / (now - last_time)
            self.speed = instant if self.speed == 0 else 0.3 * instant + 0.7 * self.speed
            last_bytes, last_time = current, now

    def _fetch_piece(self, session: requests.Session, piece: Piece) -> None:
        dest = self.parts_dir / piece.file
        tmp = dest.with_name(dest.name + ".tmp")
        headers = {"Range": f"bytes={piece.byterange[0]}-{piece.byterange[1]}"} if piece.byterange else {}
        for attempt in range(MAX_RETRIES + 1):
            got = 0
            try:
                with session.get(piece.url, headers=headers, stream=True, timeout=TIMEOUT) as r:
                    r.raise_for_status()
                    if piece.byterange and r.status_code != 206:
                        raise DownloadError(f"Server ignored the Range request (HTTP {r.status_code})")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(CHUNK_SIZE):
                            if self._stop.is_set():
                                return
                            f.write(chunk)
                            got += len(chunk)
                            with self._lock:
                                self._inflight[piece.file] = got
                            LIMITER.consume(len(chunk), self._stop)
                os.replace(tmp, dest)
                with self._lock:
                    if not piece.key:
                        self._done_sizes[piece.file] = got
                return
            except (requests.RequestException, OSError) as e:
                if isinstance(e, requests.HTTPError) and e.response is not None \
                        and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    raise
                if attempt == MAX_RETRIES:
                    raise
                self._stop.wait(min(2 ** (attempt + 1), 30))
            finally:
                with self._lock:
                    self._inflight.pop(piece.file, None)

    # --- merging ---

    def _merge(self) -> None:
        ffmpeg = find_ffmpeg()
        ext = Path(self.filename).suffix
        if not ffmpeg:
            if len(self.tracks) == 1 and not self.tracks[0].encrypted:
                out = self.parts_dir / ("output" + ext)
                self._concat(self.tracks[0], out)
                self._place(out)
                return
            raise DownloadError("ffmpeg is needed to merge this video. Install ffmpeg, "
                                "or run: pip install imageio-ffmpeg")
        inputs: list[str] = []
        for i, track in enumerate(self.tracks):
            if track.hls_playlist is not None:
                playlist = self.parts_dir / f"track{i}.m3u8"
                playlist.write_text(track.hls_playlist, encoding="utf-8")
                inputs += ["-allowed_extensions", "ALL", "-protocol_whitelist", "file,crypto,data",
                           "-i", str(playlist)]
            else:
                joined = self.parts_dir / f"track{i}.{track.container}"
                self._concat(track, joined)
                inputs += ["-i", str(joined)]
        maps = ["-map", "0:v?", "-map", "0:a?"] if len(self.tracks) == 1 else ["-map", "0:v?", "-map", "1:a?"]
        error = ""
        # Some codec combinations don't fit in MP4; MKV takes anything.
        for out_ext in dict.fromkeys([ext, ".mkv"]):
            out = self.parts_dir / ("output" + out_ext)
            code, error = self._run_ffmpeg([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                                            *inputs, *maps, "-c", "copy", str(out)])
            if self._stop.is_set():
                return
            if code == 0 and out.exists() and out.stat().st_size > 0:
                self.filename = self.stem + out_ext
                self._place(out)
                return
        last = error.strip().splitlines()[-1] if error.strip() else "unknown error"
        raise DownloadError(f"ffmpeg couldn't merge the video: {last[:300]}")

    def _concat(self, track: Track, out: Path) -> None:
        with open(out, "wb") as f:
            for piece in track.media:
                with open(self.parts_dir / piece.file, "rb") as src:
                    shutil.copyfileobj(src, f, 1024 * 1024)

    def _place(self, out: Path) -> None:
        target = self.final_path
        if target.exists():
            target = unique_path(target)
        os.replace(out, target)
        self.filename = target.name

    def _run_ffmpeg(self, cmd: list[str]) -> tuple[int, str]:
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, creationflags=flags)
        while True:
            try:
                _, err = proc.communicate(timeout=0.5)
                return proc.returncode, err.decode("utf-8", "replace")
            except subprocess.TimeoutExpired:
                if self._stop.is_set():  # paused or cancelled mid-merge
                    proc.kill()
                    proc.communicate()
                    return -1, ""
