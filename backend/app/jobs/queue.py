"""SQLite-backed asyncio job queue.

Single user, so no Celery and no Redis (doc S7). Jobs survive a restart
because the queue is a table, not an in-process list.

One GPU cannot run an LLM, SDXL and TTS at the same time (doc S9), so every
GPU-class job takes a single lock. Non-GPU work (compiling, encoding, file
copying) runs alongside it.
"""
from __future__ import annotations

import asyncio
import json
import traceback
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from .. import db, store

JobHandler = Callable[["JobContext"], Awaitable[dict]]

_handlers: dict[str, JobHandler] = {}
_subscribers: list[asyncio.Queue] = []
_gpu_lock = asyncio.Lock()
_cancelled: set[str] = set()
_running: dict[str, asyncio.Task] = {}
# The subset of `_running` that is GPU work. While `gpu_serialize` is on, only
# one of these is ever claimed at a time -- see `worker_loop`.
_running_gpu: set[str] = set()
_loop: Optional[asyncio.AbstractEventLoop] = None

# Which job kinds contend for the GPU, stated as the exceptions rather than
# the members: fan-out jobs that only enqueue other jobs, and the compile /
# export / lint path. Everything else -- every llm.*, image.*, tts.* and
# lora.* kind, the ones here today and the ones added next -- takes the lock.
#
# It was an allow-list, and it drifted: image.profile, image.cg_set,
# image.caption and image.cg_prompt all reach ComfyUI or LM Studio and none of
# them were listed, so they ran alongside a locked job and put two workflows on
# one card. Missing an entry below costs a little lost overlap on a cheap job;
# missing one from an allow-list double-books the GPU.
# `tags.` is here for the same reason as the others: importing the booru
# dictionary is HTTP and JSON, and holding the GPU lock through it would stall
# a render queue behind a download.
NON_GPU_PREFIXES = ("build.", "batch.", "tags.")
NON_GPU_KINDS: set[str] = set()


def is_gpu_kind(kind: str) -> bool:
    if kind in NON_GPU_KINDS:
        return False
    return not kind.startswith(NON_GPU_PREFIXES)


def handler(kind: str) -> Callable[[JobHandler], JobHandler]:
    def register(fn: JobHandler) -> JobHandler:
        _handlers[kind] = fn
        return fn
    return register


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobCancelled(Exception):
    pass


class JobContext:
    """Handed to a handler: its payload, plus progress reporting."""

    def __init__(self, job_id: str, project_id: str, kind: str,
                 payload: dict, node_id: Optional[str]) -> None:
        self.job_id = job_id
        self.project_id = project_id
        self.kind = kind
        self.payload = payload
        self.node_id = node_id
        self._log: list[str] = []

    async def progress(self, value: float, message: str = "") -> None:
        if self.job_id in _cancelled:
            raise JobCancelled()
        if message:
            self._log.append(message)
            del self._log[:-40]
        db.execute("UPDATE jobs SET progress = ?, label = ? WHERE id = ?",
                   (max(0.0, min(1.0, value)), message or "", self.job_id))
        await publish({"type": "job.progress", "id": self.job_id, "progress": value,
                       "label": message, "project_id": self.project_id,
                       "node_id": self.node_id, "kind": self.kind})

    async def stream(self, text: str, done: bool = False) -> None:
        """Publish the text a job has produced so far.

        The whole text every time, not a delta: an event a slow reader misses
        (see `publish`) then costs nothing, because the next one carries it.
        """
        await publish({"type": "job.stream", "id": self.job_id, "text": text,
                       "done": done, "project_id": self.project_id,
                       "node_id": self.node_id, "kind": self.kind})

    def check_cancelled(self) -> None:
        if self.job_id in _cancelled:
            raise JobCancelled()

    @property
    def log(self) -> list[str]:
        return list(self._log)


# --------------------------------------------------------------------------
# event stream
# --------------------------------------------------------------------------

async def publish(event: dict[str, Any]) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            resync(q)


def resync(q: asyncio.Queue) -> None:
    """Replace a reader's backlog with one instruction to refetch everything.

    A full queue used to drop the subscriber -- but its stream stayed open,
    sending keepalives, so the browser never reconnected and that tab simply
    stopped hearing about jobs: nothing it generated appeared in Script or
    Canvas until a manual reload. It takes very little to fill: a batch fill
    enqueues hundreds of jobs without yielding, so their `job.queued` events
    land faster than any stream can drain them. Events missed that way cannot
    be replayed one by one, so the reader is told to catch up instead.
    """
    while True:
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            break
    q.put_nowait({"type": "resync"})


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Remember the server's loop so sync request handlers can publish."""
    global _loop
    _loop = loop


def publish_threadsafe(event: dict[str, Any]) -> None:
    """Publish from a worker thread (sync endpoints run off the loop)."""
    if _loop is None or _loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(publish(event), _loop)
    except RuntimeError:
        pass


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=512)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    if q in _subscribers:
        _subscribers.remove(q)


# --------------------------------------------------------------------------
# queue api
# --------------------------------------------------------------------------

def enqueue(project_id: str, kind: str, payload: dict, label: str = "",
            node_id: Optional[str] = None) -> str:
    job_id = store.new_id("j_")
    db.execute(
        """INSERT INTO jobs (id, project_id, kind, status, progress, label, node_id,
                             payload, result, error, created_at)
           VALUES (?,?,?,'queued',0,?,?,?,'{}','',?)""",
        (job_id, project_id, kind, label, node_id, db.dumps(payload), now()),
    )
    publish_threadsafe({"type": "job.queued", "id": job_id, "kind": kind, "label": label,
                        "project_id": project_id, "node_id": node_id})
    return job_id


def get_job(job_id: str) -> Optional[dict]:
    row = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return _row_to_job(row) if row else None


def list_jobs(project_id: str, limit: int = 100, status: Optional[str] = None) -> list[dict]:
    if status:
        rows = db.query(
            "SELECT * FROM jobs WHERE project_id = ? AND status = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, status, limit),
        )
    else:
        rows = db.query(
            "SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
            (project_id, limit),
        )
    return [_row_to_job(r) for r in rows]


def _row_to_job(row) -> dict:
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "kind": row["kind"],
        "status": row["status"],
        "progress": row["progress"],
        "label": row["label"],
        "node_id": row["node_id"],
        "payload": db.loads(row["payload"], {}),
        "result": db.loads(row["result"], {}),
        "error": row["error"],
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def cancel(job_id: str) -> None:
    _cancelled.add(job_id)
    row = db.one("SELECT status FROM jobs WHERE id = ?", (job_id,))
    if row and row["status"] == "queued":
        db.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?", (now(), job_id))
    task = _running.get(job_id)
    if task:
        task.cancel()


def clear_finished(project_id: str) -> int:
    rows = db.query(
        "SELECT id FROM jobs WHERE project_id = ? AND status IN ('done','error','cancelled')",
        (project_id,),
    )
    db.execute(
        "DELETE FROM jobs WHERE project_id = ? AND status IN ('done','error','cancelled')",
        (project_id,),
    )
    return len(rows)


# --------------------------------------------------------------------------
# worker
# --------------------------------------------------------------------------

async def _run_job(row) -> None:
    job_id = row["id"]
    kind = row["kind"]
    ctx = JobContext(job_id, row["project_id"], kind, db.loads(row["payload"], {}), row["node_id"])

    fn = _handlers.get(kind)
    if fn is None:
        db.execute(
            "UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?",
            (f"no handler registered for job kind '{kind}'", now(), job_id),
        )
        await publish({"type": "job.error", "id": job_id, "kind": kind,
                       "error": f"no handler for {kind}", "project_id": row["project_id"]})
        return

    async def mark_started() -> None:
        db.execute("UPDATE jobs SET status='running', started_at=?, label=? WHERE id=?",
                   (now(), row["label"] or "", job_id))
        await publish({"type": "job.started", "id": job_id, "kind": kind,
                       "project_id": row["project_id"], "node_id": row["node_id"],
                       "label": row["label"]})

    serialize = bool(store.get_setting("gpu_serialize", True)) and is_gpu_kind(kind)
    try:
        if serialize:
            # Stay 'claimed' until the card is actually ours. Flipping to
            # 'running' before taking the lock made two jobs read as started at
            # once and left the second one's progress bar sitting at zero.
            if _gpu_lock.locked():
                db.execute("UPDATE jobs SET label=? WHERE id=?",
                           ("waiting for the GPU", job_id))
                await publish({"type": "job.waiting", "id": job_id, "kind": kind,
                               "project_id": row["project_id"], "node_id": row["node_id"],
                               "label": "waiting for the GPU"})
            async with _gpu_lock:
                # Inside the lock and before the handler: this is the only
                # moment nothing is mid-run, so it is the only moment it is
                # safe to unload another engine's models out from under it.
                swap = await _free_the_card(ctx, job_id, kind, row)
                await mark_started()
                result = await fn(ctx)
                if swap.get("swapped"):
                    result = {**(result or {}), "vram": swap}
        else:
            await mark_started()
            result = await fn(ctx)
        db.execute(
            "UPDATE jobs SET status='done', progress=1.0, result=?, finished_at=? WHERE id=?",
            (db.dumps(result or {}), now(), job_id),
        )
        await publish({"type": "job.done", "id": job_id, "kind": kind,
                       "project_id": row["project_id"], "node_id": row["node_id"],
                       "result": result or {}})
    except (JobCancelled, asyncio.CancelledError):
        db.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=?", (now(), job_id))
        await publish({"type": "job.cancelled", "id": job_id, "kind": kind,
                       "project_id": row["project_id"], "node_id": row["node_id"]})
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
        detail = f"{type(exc).__name__}: {exc}"
        db.execute(
            "UPDATE jobs SET status='error', error=?, finished_at=? WHERE id=?",
            (detail + "\n" + traceback.format_exc(limit=4), now(), job_id),
        )
        await publish({"type": "job.error", "id": job_id, "kind": kind, "error": detail,
                       "project_id": row["project_id"], "node_id": row["node_id"]})
    finally:
        _cancelled.discard(job_id)
        _running.pop(job_id, None)
        _running_gpu.discard(job_id)


async def _free_the_card(ctx: "JobContext", job_id: str, kind: str, row) -> dict:
    """Evict whichever engine is holding the GPU, if it is not this job's.

    Best effort by construction -- `vram.ensure` never raises -- because a
    render that might have fitted anyway must not be failed by a failed
    unload. What it does gets attached to the job result, so a five-second
    stall at the start of a job reads as a model swap rather than a hang.
    """
    from ..services import vram

    async def label(text: str) -> None:
        db.execute("UPDATE jobs SET label=? WHERE id=?", (text, job_id))
        await publish({"type": "job.progress", "id": job_id, "kind": kind,
                       "progress": 0.0, "label": text,
                       "project_id": row["project_id"], "node_id": row["node_id"]})

    try:
        return await vram.ensure(kind, notify=label)
    except Exception:  # noqa: BLE001 - a swap must never take the job with it
        traceback.print_exc()
        return {"swapped": False, "failures": ["the VRAM swap itself errored"]}


# How far down the queue to look for something claimable. Wider than the
# number of jobs we will actually claim, because GPU jobs get skipped while one
# is already in flight -- with a window the size of the claim budget, a queue
# headed by four renders would hide the batch.fill_missing job sitting behind
# them and starve it until they drained.
CLAIM_WINDOW = 50


async def worker_loop(poll_interval: float = 0.4, concurrency: int = 4) -> None:
    """Claim queued jobs oldest-first and run them.

    GPU work is claimed strictly one at a time while `gpu_serialize` is on.
    Claiming four and letting three sit on the lock bought nothing and cost
    determinism: each `_run_job` awaits before it reaches the lock, so the
    order four concurrent tasks arrive in is not the order they were queued
    in, and `services.vram` would then swap models at boundaries that only
    exist because the claim order drifted. One at a time makes the queue
    actually FIFO, which is what keeps an overnight batch to one swap.

    Non-GPU work -- compiling, exporting, the batch fan-out jobs -- still runs
    alongside it, which is the whole point of the concurrency budget.
    """
    while True:
        try:
            if len(_running) < concurrency:
                serialize = bool(store.get_setting("gpu_serialize", True))
                # `rowid` breaks the tie, and has to: created_at is written to
                # whole seconds, so a fan-out job that enqueues its work in a
                # deliberate order -- batch.fill_missing puts CG direction
                # ahead of the CG renders that read it -- had that order
                # decided by whatever SQLite felt like returning. rowid is
                # insertion order, which is the order that was meant.
                rows = db.query(
                    "SELECT * FROM jobs WHERE status = 'queued'"
                    " ORDER BY created_at, rowid LIMIT ?",
                    (CLAIM_WINDOW,),
                )
                for row in rows:
                    if len(_running) >= concurrency:
                        break
                    if row["id"] in _running:
                        continue
                    gpu = is_gpu_kind(row["kind"])
                    if gpu and serialize and _running_gpu:
                        # Leave it queued rather than claiming it to wait: a
                        # claimed job that is only going to block on the lock
                        # is indistinguishable from a running one in the UI.
                        continue
                    db.execute("UPDATE jobs SET status='claimed' WHERE id=?", (row["id"],))
                    task = asyncio.create_task(_run_job(row))
                    _running[row["id"]] = task
                    if gpu:
                        _running_gpu.add(row["id"])
            await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            traceback.print_exc()
            await asyncio.sleep(2.0)


def reset_stuck_jobs() -> None:
    """Anything mid-flight when the process died is not coming back."""
    db.execute(
        "UPDATE jobs SET status='error', error='interrupted by server restart', finished_at=?"
        " WHERE status IN ('running','claimed')",
        (now(),),
    )
