"""Queues and the scheduler (pydm/queues.py), against the local test server.
Times are passed to tick() explicitly, so nothing waits for a real clock.
Run from the project root: .venv/Scripts/python tests/test_queues.py
"""
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.dirname(os.path.abspath(__file__))]

import server  # noqa: E402
from pydm.engine import DownloadManager, Status  # noqa: E402
from pydm.queues import MAIN_QUEUE, QueueManager  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures.append(name)


base = server.start()
tmp = Path(tempfile.mkdtemp())
out = tmp / "files"
manager = DownloadManager(tmp / "downloads.json", max_active=5)
queues = QueueManager(manager, tmp / "queues.json")
check("a main queue exists", queues.get(MAIN_QUEUE) and queues.get(MAIN_QUEUE).name == "Main queue")


def add(name, slow=True):
    url = f"{base}/ranges/x?delay=0.02&n={name}" if slow else f"{base}/ranges/x?n={name}"
    return manager.add(url, out, 2, filename=name, start=False)


def run_until(cond, timeout=60, now=None):
    t0, events = time.time(), []
    while not cond() and time.time() - t0 < timeout:
        events += queues.tick(now)
        time.sleep(0.05)
    return events


a, b, c = add("a.bin"), add("b.bin"), add("c.bin")
queues.add_tasks(MAIN_QUEUE, [a.id, b.id, c.id])
queues.move(MAIN_QUEUE, [c.id], -2)
check("move up/down reorders", queues.get(MAIN_QUEUE).task_ids == [c.id, a.id, b.id])
queues.move(MAIN_QUEUE, [c.id], +1)
check("…both ways", queues.get(MAIN_QUEUE).task_ids == [a.id, c.id, b.id])

# Running: one at a time, in queue order.
queues.start(MAIN_QUEUE)
time.sleep(0.3)
running = [t.filename for t in (a, b, c) if t.status in Status.ACTIVE]
check("starts only max_active (1) download, the first", running == ["a.bin"], str(running))
order = []


def note_order():
    for t in (a, c, b):
        if t.status in Status.ACTIVE and t.filename not in order:
            order.append(t.filename)
    return all(t.status == Status.COMPLETED for t in (a, b, c))


t0, events = time.time(), []
while not note_order() and time.time() - t0 < 90:
    events += queues.tick()
    time.sleep(0.05)
events += queues.tick()
check("runs them one after another in queue order", order == ["a.bin", "c.bin", "b.bin"], str(order))
check("reports 'finished' when everything is done", [e for e, q in events] == ["finished"], str([e for e, q in events]))
check("…and stops", not queues.get(MAIN_QUEUE).running)

# Two at a time
q2 = queues.create("Night")
q2.max_active = 2
d, e, f = add("d.bin"), add("e.bin"), add("f.bin")
queues.add_tasks(q2.id, [d.id, e.id, f.id])
check("adding to a queue moves a task out of its old one", queues.queue_of(d.id) is q2)
queues.start(q2.id)
time.sleep(0.3)
check("max_active 2 runs two", sum(t.status in Status.ACTIVE for t in (d, e, f)) == 2)

# Pausing a download by hand: the queue ends as "stopped", never "finished" (no shutdown).
manager.pause(d)
manager.pause(e)
d.join(5)
e.join(5)
events = run_until(lambda: not q2.running and f.status == Status.COMPLETED or not q2.running, 60)
check("hand-paused downloads -> 'stopped', not 'finished'", ("stopped", q2) in events
      and ("finished", q2) not in events, str([x for x, _ in events]))

# Schedule: start Mon-Fri at 02:00, stop at 02:30. tick() gets the time explicitly.
q2.start_enabled, q2.start_time, q2.days = True, "02:00", [0, 1, 2, 3, 4]
q2.stop_enabled, q2.stop_time = True, "02:30"
saturday = datetime(2026, 10, 31, 2, 0, 20)
check("not on a day that isn't selected", queues.tick(saturday) == [] and not q2.running)
monday = datetime(2026, 11, 2, 2, 0, 5)
events = queues.tick(monday)
check("starts at the scheduled time", ("started", q2) in events and q2.running)
check("fires once per minute", queues.tick(datetime(2026, 11, 2, 2, 0, 40)) in ([], [("stopped", q2)])
      and q2.last_start == "2026-11-02 02:00")
q2.running = True  # still downloading
events = queues.tick(datetime(2026, 11, 2, 2, 30, 1))
check("stops at the stop time", ("stopped", q2) in events and not q2.running, str(events))
check("next start is the next weekday", queues.next_start(q2, datetime(2026, 10, 30, 23, 0))
      == datetime(2026, 11, 2, 2, 0), str(queues.next_start(q2, datetime(2026, 10, 30, 23, 0))))
check("schedule text", q2.schedule_text() == "Starts weekdays at 02:00, stops at 02:30", q2.schedule_text())

# Empty queue started -> no 'finished' (an empty queue must never shut the PC down)
empty = queues.create("Empty")
queues.start(empty.id)
events = queues.tick()
check("empty queue: no 'finished' event", ("finished", empty) not in events and not empty.running)

# Persistence and cleanup
manager.remove(f)
queues.remove_task(f.id)
queues.save()
reloaded = QueueManager(manager, tmp / "queues.json")
r2 = reloaded.get(q2.id)
check("queues, order and schedule survive a restart", r2 and r2.name == "Night" and r2.max_active == 2
      and r2.task_ids == [d.id, e.id] and r2.start_time == "02:00" and r2.days == [0, 1, 2, 3, 4]
      and not r2.running)
queues.delete(MAIN_QUEUE)
queues.delete(empty.id)
check("main queue can't be deleted, others can", queues.get(MAIN_QUEUE) and not queues.get(empty.id))

# Resuming after a restart (or a power cut)
g, h = add("g.bin"), add("h.bin")
night = queues.create("Overnight")
night.stop_enabled, night.stop_time = True, "07:00"
queues.add_tasks(night.id, [g.id, h.id])
queues.start(night.id)          # saves "running" right away, as a power cut wouldn't wait
time.sleep(0.3)
manager.pause(g)
g.join(5)
after = QueueManager(manager, tmp / "queues.json")   # "next start"
again = after.get(night.id)
check("a queue running at shutdown is remembered", again.interrupted and not again.running)
saved = datetime.fromisoformat(again.saved_at)
resumed = after.resume_interrupted(saved.replace(hour=6, minute=59) if saved.hour < 6 else saved)
check("…and resumed on the next start", resumed == [again] and again.running
      and g.status in (*Status.ACTIVE, Status.QUEUED), str([q.name for q in resumed]))
after.stop(night.id)
g.join(5)

queues.start(night.id)
later = QueueManager(manager, tmp / "queues.json")
q_late = later.get(night.id)
q_late.saved_at = "2026-11-02T06:30:00"
check("not resumed if its stop time passed while PyDM was closed",
      later.resume_interrupted(datetime(2026, 11, 2, 8, 0)) == [] and not q_late.running)
early = QueueManager(manager, tmp / "queues.json")
q_early = early.get(night.id)
q_early.saved_at = "2026-11-02T06:30:00"
check("…but resumed if the stop time is still ahead",
      early.resume_interrupted(datetime(2026, 11, 2, 6, 45)) == [q_early] and q_early.running)
early.stop(night.id)
queues.stop(night.id)
for t in (g, h):
    t.join(5)
check("a stopped (or finished) queue is not resumed",
      QueueManager(manager, tmp / "queues.json").resume_interrupted() == [])

manager.shutdown()
print(f"\n{len(failures)} failure(s)" + (f": {failures}" if failures else ""))
sys.exit(1 if failures else 0)
