"""Headless downloader: python -m pydm.cli URL [-o DIR] [-c CONNECTIONS] [-q QUALITY]

Works for ordinary files, HLS (.m3u8) / DASH (.mpd) videos and YouTube links
(-q audio for the soundtrack only).
Ctrl+C pauses; run the same command again to resume.
"""
import argparse
import json
import time
from pathlib import Path

from .engine import DownloadTask, Status, unique_path
from .format import human_size, human_time
from .streams import PARTS_SUFFIX, StreamTask, guess_kind, list_variants
from .ytdl import YtdlTask


def file_task(args) -> DownloadTask:
    filename = args.filename
    if filename and not (Path(args.output_dir) / (filename + ".part")).exists():
        filename = unique_path(Path(args.output_dir) / filename).name
    task = DownloadTask(args.url, args.output_dir, args.connections, filename)
    if not filename:
        # Reuse an existing partial download of the same URL so re-running resumes.
        for state in Path(args.output_dir).glob("*.part.json"):
            try:
                if json.loads(state.read_text(encoding="utf-8")).get("url") == args.url:
                    task.filename = state.name.removesuffix(".part.json")
                    break
            except (OSError, ValueError):
                continue
    return task


def stream_task(args, kind: str) -> StreamTask:
    # Resume an unfinished download of the same URL if there is one.
    for plan in Path(args.output_dir).glob(f"*{PARTS_SUFFIX}/plan.json"):
        try:
            data = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("url") == args.url and data.get("filename"):
            kind = data.get("kind", kind)
            task = task_class(kind)(args.url, args.output_dir, args.connections, kind=kind,
                                    variant=data.get("variant"))
            task.stem, task.filename = plan.parent.name.removesuffix(PARTS_SUFFIX), data["filename"]
            task._named = True
            print(f"Resuming {task.filename}")
            return task

    kind, variants = list_variants(args.url, kind=kind)
    variant = None
    if variants:
        print("Qualities: " + ", ".join(v.label for v in variants))
        if args.quality == "worst":
            variant = variants[-1]
        elif args.quality not in ("best", None):
            q = args.quality.lower()
            variant = next((v for v in variants if v.label.lower().startswith(q) or v.id == q), None)
            if variant is None:
                raise SystemExit(f"No {args.quality!r} quality; pick one of the above (e.g. 720p), best or worst")
        variant = variant or variants[0]
        print(f"Downloading {variant.label}")
    return task_class(kind)(args.url, args.output_dir, args.connections, args.filename, kind=kind,
                            variant=variant.id if variant else None)


def task_class(kind: str):
    return YtdlTask if kind == "ytdl" else StreamTask


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("url")
    p.add_argument("-o", "--output-dir", default=".")
    p.add_argument("-c", "--connections", type=int, default=8)
    p.add_argument("-f", "--filename", help="override the server-provided name")
    p.add_argument("-q", "--quality", default="best", help="videos: best (default), worst, e.g. 720p, or audio (YouTube)")
    p.add_argument("--video", choices=["hls", "dash"], help="treat the URL as a video playlist")
    args = p.parse_args()

    kind = args.video or guess_kind(args.url)
    task = stream_task(args, kind) if kind else file_task(args)

    task.start()
    try:
        while task.is_running:
            s = task.snapshot()
            total = ("~" if s["size_estimated"] else "") + human_size(s["total_size"]) if s["total_size"] else "?"
            pct = f"{100 * s['progress']:5.1f}%" if s["progress"] is not None else "  ?  "
            print(f"\r{s['status']:<11} {pct}  {human_size(s['downloaded'])}/{total}  "
                  f"{human_size(s['speed'])}/s  ETA {human_time(s['eta'])}  "
                  f"conns {s['active_connections']}   ", end="", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        task.pause()
        task.join()
    print()
    print(f"{task.status}: {task.final_path}" + (f" ({task.error})" if task.error else ""))
    raise SystemExit(0 if task.status in (Status.COMPLETED, Status.PAUSED) else 1)


if __name__ == "__main__":
    main()
