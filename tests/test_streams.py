"""HLS/DASH tests: generates real test videos with ffmpeg, serves them locally,
downloads them, and checks the output fully decodes with the right duration/tracks.
Run from the project root: .venv/Scripts/python tests/test_streams.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402
from pydm import streams  # noqa: E402
from pydm.engine import DownloadManager, Status  # noqa: E402
from pydm.streams import DrmError, LiveStreamError, StreamTask, list_variants  # noqa: E402

FF = streams.find_ffmpeg()
DURATION = 8
failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


def ff(*args, cwd):
    # Relative paths only: given absolute Windows paths, ffmpeg's HLS/DASH muxers
    # write some files into the current directory instead of next to the playlist.
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True, cwd=cwd)


def make_media(root: Path):
    src = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-f", "lavfi", "-i", "sine=frequency=440",
           "-t", DURATION]
    h264 = ["-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-c:a", "aac", "-b:a", "64k"]
    hls = ["-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod"]
    fmp4 = ["-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4"]
    for d in ("aes", "low", "high", "vid", "aud", "br", "dash1", "dash2", "dashwebm"):
        (root / d).mkdir()

    # AES-128, no IV in the playlist (IV = media sequence number), sequence starts at 5.
    (root / "aes" / "enc.key").write_bytes(os.urandom(16))
    (root / "keyinfo").write_text("enc.key\naes/enc.key\n")
    ff(*src, *h264, *hls, "-start_number", 5, "-hls_key_info_file", "keyinfo",
       "-hls_segment_filename", "aes/seg_%03d.ts", "aes/index.m3u8", cwd=root)

    # Master playlist with two muxed TS variants.
    for name, size in (("low", "320x180"), ("high", "640x360")):
        ff(*src, *h264, "-s", size, *hls, "-hls_segment_filename", f"{name}/s%03d.ts", f"{name}/index.m3u8",
           cwd=root)
    (root / "master.m3u8").write_text(
        "#EXTM3U\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=400000,RESOLUTION=320x180,CODECS="avc1.42c00d,mp4a.40.2"\nlow/index.m3u8\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=640x360,CODECS="avc1.42c01e,mp4a.40.2"\nhigh/index.m3u8\n')

    # fMP4 video with a separate audio rendition.
    ff(*src, *h264, "-an", *hls, *fmp4, "-hls_segment_filename", "vid/s%03d.m4s", "vid/index.m3u8", cwd=root)
    ff(*src, *h264, "-vn", *hls, *fmp4, "-hls_segment_filename", "aud/s%03d.m4s", "aud/index.m3u8", cwd=root)
    (root / "altaudio.m3u8").write_text(
        "#EXTM3U\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",DEFAULT=YES,URI="aud/index.m3u8"\n'
        '#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=640x360,CODECS="avc1.42c01e,mp4a.40.2",AUDIO="aud"\n'
        "vid/index.m3u8\n")

    # Single file + EXT-X-BYTERANGE.
    ff(*src, *h264, *hls, "-hls_flags", "single_file", "br/index.m3u8", cwd=root)

    # DASH: SegmentTimeline, plain $Number$ template, and WebM (VP9 + Opus).
    for name, timeline in (("dash1", 1), ("dash2", 0)):
        ff(*src, "-map", 0, "-map", 1, *h264, "-f", "dash", "-seg_duration", 1, "-use_template", 1,
           "-use_timeline", timeline, f"{name}/manifest.mpd", cwd=root)
    ff(*src, "-s", "320x180", "-map", 0, "-map", 1, "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", 8,
       "-b:v", "300k", "-c:a", "libopus", "-f", "dash", "-dash_segment_type", "webm", "-seg_duration", 1,
       "dashwebm/manifest.mpd", cwd=root)

    # DRM and live samples (we must refuse these).
    (root / "fairplay.m3u8").write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n"
        '#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://key1",KEYFORMAT="com.apple.streamingkeydelivery"\n'
        "#EXTINF:1,\nlow/s000.ts\n#EXT-X-ENDLIST\n")
    mpd = (root / "dash1/manifest.mpd").read_text()
    (root / "dash1/widevine.mpd").write_text(mpd.replace(
        "<Representation", '<ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>'
        "<Representation", 1))
    (root / "live.m3u8").write_text((root / "low/index.m3u8").read_text()
                                    .replace("#EXT-X-ENDLIST", "").replace("#EXT-X-PLAYLIST-TYPE:VOD", "")
                                    .replace("\ns", "\nlow/s"))


def inspect(path):
    """(duration seconds, [(type, codec, width, height)], decode error output)."""
    info = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", info)
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    kinds = [(m[1], m[2], m[3] or "", m[4] or "") for m in
             re.finditer(r"Stream #\S+?: (Video|Audio): (\w+)(?:.*?, (\d{2,5})x(\d{2,5}))?", info)]
    decode = subprocess.run([FF, "-hide_banner", "-v", "error", "-i", str(path), "-f", "null", "-"],
                            capture_output=True, text=True).stderr.strip()
    return duration, kinds, decode


def run(task, timeout=120):
    task.start()
    t0 = time.time()
    while task.is_running and time.time() - t0 < timeout:
        time.sleep(0.1)
    return task


def check_output(name, task, video=True, audio=True, height=None, ext=None):
    ok = task.status == Status.COMPLETED
    check(f"{name}: completes", ok, f"{task.status} {task.error or ''}")
    if not ok:
        return
    duration, kinds, decode = inspect(task.final_path)
    types = [k[0] for k in kinds]
    check(f"{name}: decodes cleanly, {DURATION}s",
          duration is not None and abs(duration - DURATION) < 0.7 and not decode,
          f"duration={duration} errors={decode[:200]!r}")
    check(f"{name}: tracks", (("Video" in types) == video) and (("Audio" in types) == audio), str(kinds))
    if height:
        heights = [int(k[3]) for k in kinds if k[0] == "Video" and k[3]]
        check(f"{name}: picked {height}p", heights == [height], str(heights))
    if ext:
        check(f"{name}: saved as {ext}", task.final_path.suffix == ext, task.final_path.name)
    check(f"{name}: parts folder removed", not task.parts_dir.exists())


def main():
    if not FF:
        print("ffmpeg not available; install imageio-ffmpeg")
        sys.exit(1)

    media = Path(tempfile.mkdtemp())
    out = Path(tempfile.mkdtemp())
    try:
        t0 = time.time()
        make_media(media)
        print(f"(generated test media in {time.time() - t0:.1f}s)")
        server.STATIC_ROOT = media
        base = server.start() + "/static"

        # Probing qualities
        kind, variants = list_variants(f"{base}/master.m3u8")
        check("HLS master lists qualities best-first", kind == "hls" and [v.height for v in variants] == [360, 180],
              str([v.label for v in variants]))
        kind, variants = list_variants(f"{base}/dash1/manifest.mpd")
        check("DASH lists video qualities", kind == "dash" and len(variants) == 1, str([v.label for v in variants]))
        for label, url, exc in (("FairPlay HLS refused", "fairplay.m3u8", DrmError),
                                ("Widevine DASH refused", "dash1/widevine.mpd", DrmError),
                                ("live HLS refused", "live.m3u8", LiveStreamError)):
            try:
                list_variants(f"{base}/{url}")
                check(label, False, "no error")
            except exc as e:
                check(label, True, f"({e})")

        # Downloads
        check_output("AES-128 HLS", run(StreamTask(f"{base}/aes/index.m3u8", out, 4, "aes")))
        check_output("HLS master, best quality", run(StreamTask(f"{base}/master.m3u8", out, 4, "best")), height=360)
        low = f"{base}/low/index.m3u8"
        check_output("HLS master, chosen quality",
                     run(StreamTask(f"{base}/master.m3u8", out, 4, "chosen", variant=low)), height=180)
        check_output("fMP4 + separate audio", run(StreamTask(f"{base}/altaudio.m3u8", out, 4, "alt")))
        check_output("HLS byte ranges", run(StreamTask(f"{base}/br/index.m3u8", out, 4, "br")))
        check_output("DASH SegmentTimeline", run(StreamTask(f"{base}/dash1/manifest.mpd", out, 4, "d1", kind="dash")))
        check_output("DASH $Number$", run(StreamTask(f"{base}/dash2/manifest.mpd", out, 4, "d2", kind="dash")))
        check_output("DASH WebM", run(StreamTask(f"{base}/dashwebm/manifest.mpd", out, 4, "dw", kind="dash")), ext=".mkv")
        drm = run(StreamTask(f"{base}/dash1/widevine.mpd", out, 4, "drm", kind="dash"))
        check("DRM download fails with explanation", drm.status == Status.ERROR and "DRM" in (drm.error or ""), drm.error)

        # Same name twice -> distinct files and parts folders
        a = StreamTask(f"{base}/low/index.m3u8", out, 4, "same")
        b = StreamTask(f"{base}/low/index.m3u8", out, 4, "same")
        a.start(), b.start()
        a.join(60), b.join(60)
        check("same-name downloads don't collide",
              a.status == b.status == Status.COMPLETED and a.final_path != b.final_path,
              f"{a.final_path.name if a.final_path else None} / {b.final_path.name if b.final_path else None}")

        # Pause, restore from saved state, resume: finished segments aren't fetched again
        server.STATIC_DELAY = 0.15
        t = StreamTask(f"{base}/high/index.m3u8", out, 2, "resume")
        t.start()
        while t.is_running and len(t.snapshot()["segments"]) == 0 or \
                t.is_running and sum(s[2] for s in t.snapshot()["segments"]) < 3:
            time.sleep(0.05)
        t.pause()
        t.join()
        done_before = {p.file for tr in t.tracks for p in tr.media if (t.parts_dir / p.file).exists()}
        check("stream pause -> Paused", t.status == Status.PAUSED and len(done_before) >= 3,
              f"{t.status}, {len(done_before)} segments kept")
        restored = StreamTask.from_dict(json.loads(json.dumps(t.to_dict())))
        check("restored task shows progress", restored.snapshot()["progress"] == len(done_before) / 8,
              str(restored.snapshot()["progress"]))
        estimate = restored.snapshot()["total_size"]
        server.STATIC_DELAY = 0
        server.stats["static"].clear()
        check_output("resumed stream", run(restored))
        actual = restored.final_path.stat().st_size if restored.final_path else 0
        check("mid-download size estimate is realistic", estimate and 0.6 < estimate / actual < 1.5,
              f"estimated {estimate}, actual {actual}")
        refetched = [p for p in server.stats["static"] if p.startswith("high/s")]
        check("resume skips finished segments", len(refetched) == 8 - len(done_before),
              f"fetched {len(refetched)} of {8 - len(done_before)} remaining")

        # Cancel removes the parts folder
        server.STATIC_DELAY = 0.15
        c = StreamTask(f"{base}/high/index.m3u8", out, 2, "cancel")
        c.start()
        while c.is_running and not c.parts_dir.exists():
            time.sleep(0.05)
        time.sleep(0.3)
        c.cancel()
        c.join()
        server.STATIC_DELAY = 0
        check("stream cancel cleans up", c.status == Status.CANCELLED and not c.parts_dir.exists(), c.status)

        # Without ffmpeg: plain TS is just joined; encrypted needs ffmpeg
        real = streams.find_ffmpeg
        streams.find_ffmpeg = lambda: None
        try:
            check_output("no-ffmpeg fallback (TS)", run(StreamTask(low, out, 4, "noff")), ext=".ts")
            enc = run(StreamTask(f"{base}/aes/index.m3u8", out, 4, "noff-aes"))
            check("no-ffmpeg + AES explains", enc.status == Status.ERROR and "ffmpeg" in (enc.error or ""), enc.error)
        finally:
            streams.find_ffmpeg = real

        # Manager integration + persistence
        mdir = Path(tempfile.mkdtemp())
        m = DownloadManager(mdir / "dl.json")
        task = m.add(f"{base}/master.m3u8", out, 4, "managed", kind="hls")
        task.join(60)
        m2 = DownloadManager(mdir / "dl.json")
        reloaded = m2.tasks[0] if m2.tasks else None
        check("manager runs and persists stream tasks",
              task.status == Status.COMPLETED and isinstance(reloaded, StreamTask)
              and reloaded.status == Status.COMPLETED and reloaded.final_path == task.final_path,
              f"{task.status} {task.error or ''}")
        snap = reloaded.snapshot() if reloaded else {}
        check("completed stream snapshot", snap.get("progress") == 1.0 and snap.get("total_size") == task.final_path.stat().st_size,
              str({k: snap.get(k) for k in ("progress", "total_size")}))
        shutil.rmtree(mdir)
    finally:
        shutil.rmtree(media, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)

    print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
