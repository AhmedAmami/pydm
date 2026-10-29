"""End-to-end engine tests against a local HTTP server.
Run from the project root: .venv/Scripts/python tests/test_engine.py
"""
import hashlib
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402
from pydm.engine import DownloadTask, DownloadManager, Status  # noqa: E402

BASE = server.start()
DATA, stats = server.DATA, server.stats
DIGEST = hashlib.sha256(DATA).hexdigest()


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def run(task, timeout=60):
    task.start()
    t0 = time.time()
    while task.is_running and time.time() - t0 < timeout:
        time.sleep(0.1)
    return task


failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


tmp = tempfile.mkdtemp()
try:
    # 1. Ranged, 8 connections
    t = run(DownloadTask(f"{BASE}/ranges/x", tmp, 8))
    check("ranged download completes", t.status == Status.COMPLETED, f"{t.status} {t.error}")
    check("filename from Content-Disposition", t.filename == "test file.bin", t.filename)
    check("ranged hash matches", t.status == Status.COMPLETED and sha(t.final_path) == DIGEST)
    check("no leftover .part/.json", not list(__import__("pathlib").Path(tmp).glob("*.part*")))

    # 2. Same name again -> unique name
    t2 = run(DownloadTask(f"{BASE}/ranges/x", tmp, 4))
    check("collision gets unique name", t2.filename == "test file (1).bin", t2.filename)

    # 3. Pause mid-way, resume in a fresh task object (simulates app restart)
    stats["ranges"].clear()
    t3 = DownloadTask(f"{BASE}/ranges/x?delay=0.01", tmp, 4, filename="paused.bin")
    t3.start()
    while t3.downloaded < 3 * 1024 * 1024:
        time.sleep(0.05)
    t3.pause()
    t3.join()
    got = t3.downloaded
    check("pause -> Paused", t3.status == Status.PAUSED, t3.status)
    check("state file written", t3.state_path.exists())
    t3b = DownloadTask.from_dict(t3.to_dict())
    check("restored progress", t3b.downloaded == got, f"{t3b.downloaded} vs {got}")
    t3b.url = f"{BASE}/ranges/x"
    stats["ranges"].clear()
    run(t3b)
    check("resumed download completes", t3b.status == Status.COMPLETED, f"{t3b.status} {t3b.error}")
    check("resumed hash matches", sha(t3b.final_path) == DIGEST)
    resent = sum(e - s + 1 for s, e in stats["ranges"][1:])  # skip probe
    check("resume didn't refetch finished bytes",
          resent <= len(DATA) - got + 1, f"refetched {resent} of {len(DATA) - got} remaining")

    # 4. Work stealing: 2 connections; segments should get split
    # Segment 0 is served slowly, so the other 3 finish first and must steal from it.
    t4 = DownloadTask(f"{BASE}/ranges/x?slow0=0.02", tmp, 4, filename="steal.bin")
    t4.start()
    while t4.status != Status.DOWNLOADING and t4.is_running:
        time.sleep(0.01)
    max_segs = 0
    while t4.is_running:
        max_segs = max(max_segs, len(t4.snapshot()["segments"]))
        time.sleep(0.05)
    check("steal download completes", t4.status == Status.COMPLETED and sha(t4.final_path) == DIGEST,
          f"{t4.status} {t4.error}")
    check("work stealing split segments", max_segs > 4, f"max segments seen: {max_segs}")

    # 5. No range support -> single connection
    t5 = run(DownloadTask(f"{BASE}/norange/x", tmp, 8, filename="norange.bin"))
    check("non-ranged completes", t5.status == Status.COMPLETED and sha(t5.final_path) == DIGEST,
          f"{t5.status} {t5.error}")
    check("non-ranged flagged", not t5.supports_ranges)

    # 6. Unknown length (chunked)
    t6 = run(DownloadTask(f"{BASE}/chunked/x", tmp, 8, filename="chunked.bin"))
    check("chunked completes", t6.status == Status.COMPLETED and sha(t6.final_path) == DIGEST,
          f"{t6.status} {t6.error}")
    check("chunked size learned", t6.total_size == len(DATA), str(t6.total_size))

    # 7. Cancel removes partial files
    t7 = DownloadTask(f"{BASE}/ranges/x?delay=0.01", tmp, 4, filename="cancel.bin")
    t7.start()
    while t7.downloaded < 1024 * 1024:
        time.sleep(0.05)
    t7.cancel()
    t7.join()
    check("cancel -> Cancelled", t7.status == Status.CANCELLED, t7.status)
    check("cancel deletes partial", not t7.part_path.exists() and not t7.state_path.exists())

    # 8. 404 -> Error, quickly (no retry storm); unreachable host -> Error
    t0 = time.time()
    t8 = run(DownloadTask(f"{BASE}/missing", tmp, 4), timeout=30)
    check("404 -> Error fast", t8.status == Status.ERROR and time.time() - t0 < 5,
          f"{t8.status} in {time.time() - t0:.1f}s: {t8.error}")
    t8b = run(DownloadTask("http://127.0.0.1:1/x", tmp, 4), timeout=30)
    check("unreachable -> Error", t8b.status == Status.ERROR, f"{t8b.status} {t8b.error}")

    # 9. Manager: queue limit + persistence
    mdir = tempfile.mkdtemp()
    m = DownloadManager(os.path.join(mdir, "dl.json"), max_active=2)
    ts = [m.add(f"{BASE}/ranges/x?delay=0.002", tmp, 2, filename=f"q{i}.bin") for i in range(4)]
    time.sleep(0.3)
    active = sum(1 for t in ts if t.status in Status.ACTIVE)
    check("manager respects max_active", active <= 2, f"active={active}")
    t0 = time.time()
    while any(t.status != Status.COMPLETED for t in ts) and time.time() - t0 < 60:
        time.sleep(0.1)
    check("manager drains queue", all(t.status == Status.COMPLETED for t in ts),
          str([t.status for t in ts]))
    m2 = DownloadManager(os.path.join(mdir, "dl.json"))
    check("manager persists task list", len(m2.tasks) == 4 and all(t.status == Status.COMPLETED for t in m2.tasks))
    shutil.rmtree(mdir)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
