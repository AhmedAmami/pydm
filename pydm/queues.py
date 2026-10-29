"""Download queues and their schedules.

A queue is an ordered list of downloads that run a few at a time (normally one),
either when started by hand or at a scheduled time, optionally stopping at
another time. When everything in a running queue has finished, its "when done"
action can exit PyDM, put the computer to sleep or shut it down.

No Qt here: the window calls tick() about once a second and acts on the events
it returns (notifications, the countdown before a shutdown). Queues are saved
to queues.json next to the download list; membership is by task id, so the
download tasks themselves don't know about queues.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path

from .engine import Status

MAIN_QUEUE = "main"
AFTER_ACTIONS = {
    "nothing": "Do nothing",
    "exit": "Exit PyDM",
    "sleep": "Put the computer to sleep",
    "shutdown": "Shut down the computer",
}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Queue:
    id: str
    name: str
    task_ids: list[str] = field(default_factory=list)
    max_active: int = 1                  # downloads from this queue at the same time
    start_enabled: bool = False
    start_time: str = "02:00"            # HH:MM, local time
    days: list[int] = field(default_factory=lambda: list(range(7)))  # 0 = Monday
    stop_enabled: bool = False
    stop_time: str = "07:00"
    after: str = "nothing"               # a key of AFTER_ACTIONS
    last_start: str = ""                 # "YYYY-MM-DD HH:MM" a scheduled start last fired
    last_stop: str = ""
    running: bool = False
    saved_at: str = ""                   # when a running queue was last saved (ISO time)

    def __post_init__(self):
        self.tried: set[str] = set()     # tasks this run has already started (not saved)
        self.interrupted = False         # was running when PyDM (or the PC) went down

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def schedule_text(self) -> str:
        if not self.start_enabled:
            return "Starts when you start it"
        days = sorted(set(self.days))
        when = ("daily" if len(days) == 7 else "weekdays" if days == list(range(5))
                else "weekends" if days == [5, 6] else ", ".join(DAY_NAMES[d] for d in days) if days else "never")
        text = f"Starts {when} at {self.start_time}"
        if self.stop_enabled:
            text += f", stops at {self.stop_time}"
        return text


class QueueManager:
    def __init__(self, manager, path: str | os.PathLike):
        self.manager = manager
        self.path = Path(path)
        self.queues: list[Queue] = []
        self._lock = threading.RLock()
        self._load()
        if not self.get(MAIN_QUEUE):
            self.queues.insert(0, Queue(MAIN_QUEUE, "Main queue"))
            self.save()

    # --- persistence ---

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        known = {t.id for t in self.manager.tasks}
        names = {f.name for f in fields(Queue)}
        for d in data:
            q = Queue(**{k: v for k, v in d.items() if k in names})
            q.task_ids = [i for i in q.task_ids if i in known]  # downloads deleted meanwhile
            q.interrupted, q.running = q.running, False  # resume_interrupted() decides
            self.queues.append(q)

    def save(self) -> None:
        with self._lock:
            now = datetime.now().isoformat(timespec="seconds")
            for q in self.queues:
                if q.running:
                    q.saved_at = now
            data = [q.to_dict() for q in self.queues]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # --- queues ---

    def get(self, queue_id: str) -> Queue | None:
        return next((q for q in self.queues if q.id == queue_id), None)

    def queue_of(self, task_id: str) -> Queue | None:
        return next((q for q in self.queues if task_id in q.task_ids), None)

    def create(self, name: str) -> Queue:
        with self._lock:
            q = Queue(uuid.uuid4().hex[:8], name.strip() or "New queue")
            self.queues.append(q)
        self.save()
        return q

    def delete(self, queue_id: str) -> None:
        """Delete a queue; its downloads stay in the list. The main queue can't be deleted."""
        if queue_id == MAIN_QUEUE:
            return
        with self._lock:
            q = self.get(queue_id)
            if q:
                self.stop(q.id, pause=False)
                self.queues.remove(q)
        self.save()

    def tasks(self, queue_id: str) -> list:
        q = self.get(queue_id)
        if not q:
            return []
        by_id = {t.id: t for t in self.manager.tasks}
        return [by_id[i] for i in q.task_ids if i in by_id]

    # --- membership and order ---

    def add_tasks(self, queue_id: str, task_ids: list[str]) -> None:
        with self._lock:
            target = self.get(queue_id)
            if not target:
                return
            for task_id in task_ids:
                for q in self.queues:
                    if task_id in q.task_ids and q is not target:
                        q.task_ids.remove(task_id)
                if task_id not in target.task_ids:
                    target.task_ids.append(task_id)
        self.save()

    def remove_task(self, task_id: str) -> None:
        with self._lock:
            for q in self.queues:
                if task_id in q.task_ids:
                    q.task_ids.remove(task_id)
                q.tried.discard(task_id)
        self.save()

    def move(self, queue_id: str, task_ids: list[str], delta: int) -> None:
        """Move tasks up (delta < 0) or down within their queue, keeping their order."""
        with self._lock:
            q = self.get(queue_id)
            if not q:
                return
            ids = q.task_ids
            picked = [i for i in ids if i in task_ids]
            order = sorted(picked, key=ids.index, reverse=delta > 0)
            for task_id in order:
                i = ids.index(task_id)
                j = max(0, min(len(ids) - 1, i + delta))
                if ids[j] in picked and j != i:
                    continue  # blocked by another moving task at the edge
                ids.insert(j, ids.pop(i))
        self.save()

    # --- running ---

    def start(self, queue_id: str) -> None:
        q = self.get(queue_id)
        if not q:
            return
        with self._lock:
            q.running = True
            q.tried = set()
        self._fill(q)
        self.save()  # so a crash or power cut still knows it was running

    def stop(self, queue_id: str, pause: bool = True) -> None:
        q = self.get(queue_id)
        if not q:
            return
        with self._lock:
            q.running = False
        if pause:
            for t in self.tasks(q.id):
                if t.status in (*Status.ACTIVE, Status.QUEUED):
                    self.manager.pause(t)
        self.save()

    def is_active(self, task) -> bool:
        return task.status in (*Status.ACTIVE, Status.QUEUED)

    def pending(self, q: Queue) -> list:
        return [t for t in self.tasks(q.id) if t.status != Status.COMPLETED and t.id not in q.tried
                and not self.is_active(t)]

    def _fill(self, q: Queue) -> None:
        """Start the next downloads in order until the queue's limit is reached."""
        with self._lock:
            active = sum(self.is_active(t) for t in self.tasks(q.id))
            for t in self.pending(q):
                if active >= q.max_active:
                    break
                q.tried.add(t.id)
                if t.status in (*Status.RESUMABLE, Status.CANCELLED):
                    self.manager.resume(t)
                    active += 1

    def tick(self, now: datetime | None = None) -> list[tuple[str, Queue]]:
        """Run schedules and keep running queues going. Returns events:
        ("started"|"stopped"|"finished", queue). "finished" means everything ran."""
        now = now or datetime.now()
        minute, hhmm = now.strftime("%Y-%m-%d %H:%M"), now.strftime("%H:%M")
        events, changed = [], False
        for q in list(self.queues):
            if q.start_enabled and hhmm == q.start_time and now.weekday() in q.days and q.last_start != minute:
                q.last_start, changed = minute, True
                if not q.running:
                    self.start(q.id)
                    events.append(("started", q))
            if q.stop_enabled and hhmm == q.stop_time and q.last_stop != minute:
                q.last_stop, changed = minute, True
                if q.running:
                    self.stop(q.id)
                    events.append(("stopped", q))
            if q.running:
                self._fill(q)
                tasks = self.tasks(q.id)
                if not any(self.is_active(t) for t in tasks) and not self.pending(q):
                    q.running, changed = False, True  # saved: a finished queue isn't resumed later
                    ran = [t for t in tasks if t.id in q.tried]
                    # "finished" (which may shut the PC down) only if everything it started
                    # ran to the end; a download paused by hand means the user stepped in.
                    if ran and all(t.status in (Status.COMPLETED, Status.ERROR) for t in ran):
                        events.append(("finished", q))
                    else:
                        events.append(("stopped", q))
        if changed:
            self.save()
        return events

    def resume_interrupted(self, now: datetime | None = None) -> list[Queue]:
        """Restart queues that were running when PyDM closed or the PC went down,
        unless their stop time passed in the meantime. Returns the restarted ones."""
        now = now or datetime.now()
        resumed = []
        for q in self.queues:
            if not q.interrupted:
                continue
            q.interrupted = False
            if q.stop_enabled and q.saved_at and _crossed(q.stop_time, datetime.fromisoformat(q.saved_at), now):
                continue
            if self.pending(q):
                self.start(q.id)
                resumed.append(q)
        return resumed

    def next_start(self, q: Queue, now: datetime | None = None) -> datetime | None:
        if not q.start_enabled or not q.days:
            return None
        now = now or datetime.now()
        h, m = map(int, q.start_time.split(":"))
        for days_ahead in range(8):
            day = (now + timedelta(days=days_ahead)).replace(hour=h, minute=m, second=0, microsecond=0)
            if day.weekday() in q.days and day > now:
                return day
        return None


def _crossed(hhmm: str, since: datetime, until: datetime) -> bool:
    """Did the clock pass HH:MM between `since` and `until`?"""
    h, m = map(int, hhmm.split(":"))
    day = since.replace(hour=h, minute=m, second=0, microsecond=0)
    while day <= until:
        if since < day <= until:
            return True
        day += timedelta(days=1)
    return False


def power_action(action: str) -> None:
    """Sleep or shut down Windows. Called after the countdown the window shows."""
    if os.name != "nt":
        return
    import subprocess
    if action == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", "0"], creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    elif action == "sleep":
        import ctypes
        ctypes.windll.powrprof.SetSuspendState(False, True, False)
