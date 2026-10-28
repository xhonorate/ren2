"""The swap as the user actually meets it: through the job queue.

`test_vram.py` covers the residency logic in isolation. What matters here is
the integration -- that a queued job triggers the swap *before* its handler
runs, so the prompt is never sent to an engine whose model is not in memory
yet, and that consecutive jobs on the same engine do not pay for a swap.

The engines are stubbed; the queue, the GPU lock and the claim order are real.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(scope="module")
def app_modules():
    tmp = tempfile.mkdtemp(prefix="vn_vramq_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from app import db, store
    db.init_db()
    from app.jobs import handlers, queue  # noqa: F401 - registers handlers
    from app.services import vram
    project = store.create_project("VRAM queue")
    return {"queue": queue, "vram": vram, "store": store, "project": project.id}


# Two test-only kinds, one per engine. Registered as real handlers so the
# worker treats them exactly like any other GPU job -- `is_gpu_kind` keys off
# the prefix, and neither is in the non-GPU set.
COMFY_KIND = "image.__vramtest"
LM_KIND = "llm.__vramtest"


@pytest.fixture
def harness(app_modules):
    queue, vram = app_modules["queue"], app_modules["vram"]

    events: list[tuple[str, object]] = []

    @queue.handler(COMFY_KIND)
    async def _comfy_job(ctx):
        events.append(("ran", COMFY_KIND, vram.resident()))
        return {"ok": True}

    @queue.handler(LM_KIND)
    async def _lm_job(ctx):
        events.append(("ran", LM_KIND, vram.resident()))
        return {"ok": True}

    async def fake_evict(engine):
        events.append(("evict", engine))
        return True, f"unloaded {engine}"

    async def fake_restore():
        events.append(("load", vram.LM))
        return True, "loaded the test model"

    async def fake_loaded_models():
        # Nothing is ever resident, so every LM job must go through a load --
        # which is what makes "the prompt is sent after the load" testable.
        return []

    vram.ENGINE_BY_KIND[COMFY_KIND] = vram.COMFY
    vram.ENGINE_BY_KIND[LM_KIND] = vram.LM
    real = (vram._evict, vram._restore_llm, vram.lmstudio.loaded_models)
    vram._evict, vram._restore_llm = fake_evict, fake_restore
    vram.lmstudio.loaded_models = fake_loaded_models
    vram.forget()
    try:
        yield events
    finally:
        vram._evict, vram._restore_llm, vram.lmstudio.loaded_models = real
        vram.ENGINE_BY_KIND.pop(COMFY_KIND, None)
        vram.ENGINE_BY_KIND.pop(LM_KIND, None)
        queue._handlers.pop(COMFY_KIND, None)
        queue._handlers.pop(LM_KIND, None)
        vram.forget()


async def _drain(queue, job_ids, timeout=20.0):
    """Run the real worker until every job has finished."""
    worker = asyncio.create_task(queue.worker_loop(poll_interval=0.05))
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            jobs = [queue.get_job(j) for j in job_ids]
            if all(j and j["status"] in ("done", "error", "cancelled") for j in jobs):
                return jobs
            await asyncio.sleep(0.05)
        raise AssertionError("jobs did not finish")
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


def test_an_image_job_unloads_the_llm_before_it_runs(app_modules, harness):
    """The first half of the ask: queue a render while the LLM holds the card
    and the render starts by itself, with the LLM gone."""
    queue, vram, pid = app_modules["queue"], app_modules["vram"], app_modules["project"]
    job = queue.enqueue(pid, COMFY_KIND, {}, label="render")
    jobs = asyncio.run(_drain(queue, [job]))

    assert jobs[0]["status"] == "done", jobs[0].get("error")
    # The eviction is before the handler, not merely alongside it.
    assert harness[0] == ("evict", vram.LM)
    assert harness[1] == ("ran", COMFY_KIND, [vram.COMFY])
    # And the swap is reported on the job, so the stall is explicable.
    assert jobs[0]["result"]["vram"]["notes"] == ["unloaded lm_studio"]


def test_comfyui_stays_loaded_across_consecutive_image_jobs(app_modules, harness):
    """The second half: more renders must not each pay for a swap."""
    queue, vram, pid = app_modules["queue"], app_modules["vram"], app_modules["project"]
    ids = [queue.enqueue(pid, COMFY_KIND, {}, label=f"render {i}") for i in range(3)]
    jobs = asyncio.run(_drain(queue, ids))

    assert all(j["status"] == "done" for j in jobs), [j.get("error") for j in jobs]
    # Exactly one eviction for the whole run, at the start.
    assert [e for e in harness if e[0] == "evict"] == [("evict", vram.LM)]
    assert len([e for e in harness if e[0] == "ran"]) == 3
    # Only the first job reports a swap; the rest found the card already theirs.
    assert "vram" in jobs[0]["result"]
    assert all("vram" not in j["result"] for j in jobs[1:])


def test_an_llm_job_frees_comfyui_and_loads_the_model_before_prompting(
        app_modules, harness):
    """The third half, and the one that actually bites: the model has to be in
    memory *before* the handler sends its prompt, or LM Studio answers 400."""
    queue, vram, pid = app_modules["queue"], app_modules["vram"], app_modules["project"]
    first = queue.enqueue(pid, COMFY_KIND, {}, label="render")
    asyncio.run(_drain(queue, [first]))
    harness.clear()

    second = queue.enqueue(pid, LM_KIND, {}, label="write")
    jobs = asyncio.run(_drain(queue, [second]))

    assert jobs[0]["status"] == "done", jobs[0].get("error")
    # Free ComfyUI, load the LLM, and only then run the handler -- in that order.
    assert harness == [
        ("evict", vram.COMFY),
        ("load", vram.LM),
        ("ran", LM_KIND, [vram.LM]),
    ], harness


def test_a_mixed_queue_swaps_at_each_boundary_and_nowhere_else(app_modules, harness):
    """Two renders, two writes: one swap at the change of engine, not four."""
    queue, vram, pid = app_modules["queue"], app_modules["vram"], app_modules["project"]
    ids = [queue.enqueue(pid, COMFY_KIND, {}, label="render 1"),
           queue.enqueue(pid, COMFY_KIND, {}, label="render 2"),
           queue.enqueue(pid, LM_KIND, {}, label="write 1"),
           queue.enqueue(pid, LM_KIND, {}, label="write 2")]
    jobs = asyncio.run(_drain(queue, ids))
    assert all(j["status"] == "done" for j in jobs), [j.get("error") for j in jobs]

    ran = [e[1] for e in harness if e[0] == "ran"]
    assert ran == [COMFY_KIND, COMFY_KIND, LM_KIND, LM_KIND], (
        "the queue must stay FIFO -- rowid breaks the whole-second created_at tie")

    evictions = [e[1] for e in harness if e[0] == "evict"]
    assert evictions == [vram.LM, vram.COMFY], (
        f"expected one swap per engine boundary, got {evictions}")


def test_only_one_gpu_job_is_in_flight_at_a_time(app_modules, harness):
    """The claim order has to be the queue order, or vram swaps at boundaries
    that only exist because four tasks raced to the lock."""
    queue, pid = app_modules["queue"], app_modules["project"]
    seen: list[int] = []

    @queue.handler("image.__vramtest_slow")
    async def _slow(ctx):
        seen.append(len(queue._running_gpu))
        await asyncio.sleep(0.05)      # long enough for another claim sweep
        return {"ok": True}

    app_modules["vram"].ENGINE_BY_KIND["image.__vramtest_slow"] = app_modules["vram"].COMFY
    try:
        ids = [queue.enqueue(pid, "image.__vramtest_slow", {}, label=f"j{i}")
               for i in range(5)]
        jobs = asyncio.run(_drain(queue, ids))
        assert all(j["status"] == "done" for j in jobs)
        assert seen == [1, 1, 1, 1, 1], (
            f"more than one GPU job was claimed at once: {seen}")
    finally:
        queue._handlers.pop("image.__vramtest_slow", None)
        app_modules["vram"].ENGINE_BY_KIND.pop("image.__vramtest_slow", None)


def test_non_gpu_work_is_not_starved_behind_a_queue_of_renders(app_modules, harness):
    """Claiming one GPU job at a time must not hide the batch fan-out job
    sitting behind a wall of renders -- that job is what queues the rest."""
    queue, pid = app_modules["queue"], app_modules["project"]
    order: list[str] = []

    @queue.handler("image.__vramtest_block")
    async def _blocker(ctx):
        await asyncio.sleep(0.08)
        order.append("gpu")
        return {"ok": True}

    @queue.handler("batch.__vramtest_fanout")
    async def _fanout(ctx):
        order.append("batch")
        return {"ok": True}

    app_modules["vram"].ENGINE_BY_KIND["image.__vramtest_block"] = app_modules["vram"].COMFY
    try:
        gpu_ids = [queue.enqueue(pid, "image.__vramtest_block", {}, label=f"r{i}")
                   for i in range(4)]
        # Queued last, behind every render.
        batch_id = queue.enqueue(pid, "batch.__vramtest_fanout", {}, label="fan out")

        jobs = asyncio.run(_drain(queue, gpu_ids + [batch_id]))
        assert all(j["status"] == "done" for j in jobs), [j.get("error") for j in jobs]
        # It must not have waited for all four renders to drain.
        assert order[0] == "batch", (
            f"non-GPU work was starved behind the GPU queue: {order}")
    finally:
        queue._handlers.pop("image.__vramtest_block", None)
        queue._handlers.pop("batch.__vramtest_fanout", None)
        app_modules["vram"].ENGINE_BY_KIND.pop("image.__vramtest_block", None)


def test_turning_the_setting_off_leaves_the_queue_alone(app_modules, harness):
    queue, vram, pid = app_modules["queue"], app_modules["vram"], app_modules["project"]
    app_modules["store"].set_setting("vram_exclusive", False)
    try:
        job = queue.enqueue(pid, COMFY_KIND, {}, label="render")
        jobs = asyncio.run(_drain(queue, [job]))
        assert jobs[0]["status"] == "done"
        assert [e for e in harness if e[0] == "evict"] == []
        assert "vram" not in jobs[0]["result"]
    finally:
        app_modules["store"].set_setting("vram_exclusive", True)
