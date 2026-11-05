"""A small persistent job queue: one worker per lane, jobs saved to a JSON file."""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable, Optional


class Cancelled(Exception):
    pass


class Job(dict):
    """id, kind, lane, payload, status, progress, message, log, result, error, times."""

    def log(self, msg: str) -> None:
        self.setdefault("log", []).append(f"{time.strftime('%H:%M:%S')} {msg}")
        self["log"] = self["log"][-400:]

    def progress(self, frac: float, message: Optional[str] = None) -> None:
        self["progress"] = max(0.0, min(1.0, float(frac)))
        if message:
            self["message"] = message


Handler = Callable[[Job, Any], Any]


class Queue:
    def __init__(self, path: Path, lanes: list[str]):
        self.path = path
        self.jobs: dict[str, Job] = {}
        self.handlers: dict[str, tuple[str, Handler]] = {}
        self.lanes = lanes
        self.lock = threading.RLock()
        self.cancel_flags: set[str] = set()
        self.ctx: Any = None
        self._load()
        self._threads: list[threading.Thread] = []

    # ---- registry
    def register(self, kind: str, lane: str, fn: Handler) -> None:
        self.handlers[kind] = (lane, fn)

    # ---- persistence
    def _load(self) -> None:
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                for d in json.load(f):
                    j = Job(d)
                    if j.get("status") == "running":   # interrupted by a restart
                        j["status"] = "queued"; j["progress"] = 0.0; j.log("requeued after restart")
                    self.jobs[j["id"]] = j

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(self.jobs.values()), f, indent=0)
        tmp.replace(self.path)

    # ---- api
    def submit(self, kind: str, payload: dict[str, Any], label: str = "") -> Job:
        if kind not in self.handlers:
            raise KeyError(f"unknown job kind {kind}")
        lane = self.handlers[kind][0]
        j = Job(id=uuid.uuid4().hex[:10], kind=kind, lane=lane, payload=payload, label=label or kind,
                status="queued", progress=0.0, message="", log=[], result=None, error=None,
                created=time.time(), started=None, finished=None)
        with self.lock:
            self.jobs[j["id"]] = j
            self._save()
        return j

    def cancel(self, job_id: str) -> Optional[Job]:
        with self.lock:
            j = self.jobs.get(job_id)
            if not j:
                return None
            if j["status"] == "queued":
                j["status"] = "cancelled"; j["finished"] = time.time()
            elif j["status"] == "running":
                self.cancel_flags.add(job_id)
            self._save()
            return j

    def remove_finished(self) -> int:
        with self.lock:
            gone = [k for k, j in self.jobs.items() if j["status"] in ("done", "failed", "cancelled")]
            for k in gone:
                del self.jobs[k]
            self._save()
        return len(gone)

    def retry(self, job_id: str) -> Optional[Job]:
        with self.lock:
            j = self.jobs.get(job_id)
            if j and j["status"] in ("failed", "cancelled"):
                j["status"] = "queued"; j["progress"] = 0.0; j["error"] = None; j["finished"] = None
                j.log("retried"); self._save()
            return j

    def list(self) -> list[Job]:
        with self.lock:
            return sorted(self.jobs.values(), key=lambda j: j["created"], reverse=True)

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self.cancel_flags

    # ---- workers
    def start(self) -> None:
        for lane in self.lanes:
            t = threading.Thread(target=self._worker, args=(lane,), daemon=True, name=f"lane-{lane}")
            t.start(); self._threads.append(t)

    def _next(self, lane: str) -> Optional[Job]:
        with self.lock:
            for j in sorted(self.jobs.values(), key=lambda j: j["created"]):
                if j["status"] == "queued" and j["lane"] == lane:
                    j["status"] = "running"; j["started"] = time.time(); j.log("started")
                    self._save()
                    return j
        return None

    def _worker(self, lane: str) -> None:
        while True:
            j = self._next(lane)
            if j is None:
                time.sleep(1.0)
                continue
            fn = self.handlers[j["kind"]][1]
            try:
                result = fn(j, self.ctx)
                with self.lock:
                    j["status"] = "cancelled" if self.is_cancelled(j["id"]) else "done"
                    j["result"] = result; j["progress"] = 1.0; j["finished"] = time.time()
            except Cancelled:
                with self.lock:
                    j["status"] = "cancelled"; j["finished"] = time.time(); j.log("cancelled")
            except Exception as e:  # noqa: BLE001 — a failed job is reported, not raised
                with self.lock:
                    j["status"] = "failed"; j["error"] = f"{type(e).__name__}: {e}"; j["finished"] = time.time()
                    j.log(traceback.format_exc()[-1500:])
            finally:
                self.cancel_flags.discard(j["id"])
                with self.lock:
                    self._save()
