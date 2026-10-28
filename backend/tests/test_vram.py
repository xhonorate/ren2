"""VRAM residency: who holds the card, and who gets evicted for whom.

The point of these is the invariant, not the plumbing: every GPU job kind must
name the engine it needs, and a job must never run while the other engine is
believed to be holding dedicated VRAM.
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
    tmp = tempfile.mkdtemp(prefix="vn_vram_")
    os.environ["VN_DATA_DIR"] = tmp
    for mod in [m for m in list(sys.modules) if m.startswith("app")]:
        del sys.modules[mod]
    from app import db, store
    db.init_db()
    from app.jobs import handlers, queue
    from app.services import vram
    return {"vram": vram, "queue": queue, "handlers": handlers, "store": store}


@pytest.fixture(autouse=True)
def reset(app_modules):
    app_modules["vram"].forget()
    yield
    app_modules["vram"].forget()


def test_every_gpu_job_kind_names_an_engine(app_modules):
    """The map is per kind rather than per prefix because the prefixes lie --
    `image.cg_prompt` is an LM Studio call. That only stays true if a new GPU
    kind cannot be added without landing here, so this is the guard."""
    vram, queue = app_modules["vram"], app_modules["queue"]

    gpu_kinds = {k for k in queue._handlers if queue.is_gpu_kind(k)}
    unmapped = gpu_kinds - set(vram.ENGINE_BY_KIND)
    assert not unmapped, (
        f"GPU job kind(s) with no engine: {sorted(unmapped)} -- add them to "
        "services.vram.ENGINE_BY_KIND")

    # And nothing non-GPU should be in there claiming the card.
    stray = {k for k in vram.ENGINE_BY_KIND if not queue.is_gpu_kind(k)}
    assert not stray, f"non-GPU kinds mapped to an engine: {sorted(stray)}"


def test_the_cg_director_is_an_llm_job_despite_its_name(app_modules):
    """The trap a prefix map would fall into: image.cg_prompt reaches LM Studio
    and never touches ComfyUI, so evicting the LLM for it would unload the very
    model it is about to call."""
    vram = app_modules["vram"]
    assert vram.engine_for("image.cg_prompt") == vram.LM
    assert vram.engine_for("image.generate") == vram.COMFY
    assert vram.engine_for("image.caption") == vram.COMFY
    assert vram.engine_for("tts.line") == vram.COMFY
    assert vram.engine_for("llm.summarize") == vram.LM
    assert vram.engine_for("lora.train") == vram.TRAINER


def test_an_unmapped_kind_evicts_everything(app_modules):
    """Fail-safe direction: an unknown kind pays for a reload rather than
    risking an OOM partway through work that has already cost GPU time."""
    vram = app_modules["vram"]
    assert vram.engine_for("image.something_new") == vram.UNKNOWN

    evicted = []
    swap = _run(vram, "image.something_new", evicted)
    assert swap["swapped"] is True
    assert set(evicted) == {vram.LM, vram.COMFY}


def _run(vram, kind, evicted, notes=None, llm_loaded=True, loads=None):
    """Drive `ensure` with the eviction and load calls stubbed out.

    `llm_loaded` says whether LM Studio is pretending to have a model in
    memory; when it does not, the restore path runs and appends to `loads`.
    Both are stubbed because the real ones reach LM Studio over HTTP and shell
    out to `lms` -- a unit test must not unload the developer's actual model.
    """
    async def fake_evict(engine):
        evicted.append(engine)
        return True, (notes or {}).get(engine, f"unloaded {engine}")

    async def fake_loaded_models():
        return ["some-model"] if llm_loaded else []

    async def fake_restore():
        (loads if loads is not None else []).append("restore")
        return True, "loaded some-model"

    real = (vram._evict, vram.lmstudio.loaded_models, vram._restore_llm)
    vram._evict = fake_evict
    vram.lmstudio.loaded_models = fake_loaded_models
    vram._restore_llm = fake_restore
    try:
        return asyncio.run(vram.ensure(kind))
    finally:
        vram._evict, vram.lmstudio.loaded_models, vram._restore_llm = real


def test_the_first_job_of_a_session_sweeps_the_other_engine(app_modules):
    """Residency starts unknown, and it has to: the user may well have both
    apps open with models loaded, which is the whole reason this exists."""
    vram = app_modules["vram"]
    evicted = []
    swap = _run(vram, "image.generate", evicted)
    assert swap["swapped"] is True
    # ComfyUI is what this job needs, so only the LLM is asked to let go.
    assert evicted == [vram.LM]
    assert vram.resident() == [vram.COMFY]


def test_a_second_job_on_the_same_engine_does_not_swap(app_modules):
    """The swap costs a model load, so a run of image jobs must pay it once."""
    vram = app_modules["vram"]
    _run(vram, "image.generate", [])
    evicted = []
    swap = _run(vram, "image.sprite", evicted)
    assert swap["swapped"] is False
    assert swap["reason"] == "already resident"
    assert evicted == []


def test_switching_engines_evicts_the_incumbent(app_modules):
    vram = app_modules["vram"]
    _run(vram, "image.generate", [])          # ComfyUI now holds the card
    evicted = []
    swap = _run(vram, "image.cg_prompt", evicted)   # the director wants the LLM
    assert swap["swapped"] is True
    assert evicted == [vram.COMFY]
    assert vram.resident() == [vram.LM]


def test_a_failed_eviction_does_not_block_the_job(app_modules):
    """A missing `lms` must cost a warning, not a dead queue: the render might
    have fitted anyway, and failing it outright would be worse."""
    vram = app_modules["vram"]

    async def failing_evict(engine):
        return False, "`lms` is not on PATH"

    real = vram._evict
    vram._evict = failing_evict
    try:
        swap = asyncio.run(vram.ensure("image.generate"))
    finally:
        vram._evict = real

    assert swap["swapped"] is True
    assert swap["failures"] and "not on PATH" in swap["failures"][0]
    # Recorded as held regardless, or every later job retries the same failure.
    assert vram.resident() == [vram.COMFY]


def test_turning_the_setting_off_disables_swapping_entirely(app_modules):
    """Two GPUs, or a card big enough to co-resident: no evictions at all."""
    vram, store = app_modules["vram"], app_modules["store"]
    store.set_setting("vram_exclusive", False)
    try:
        evicted = []
        swap = _run(vram, "image.generate", evicted)
        assert swap["swapped"] is False
        assert evicted == []
    finally:
        store.set_setting("vram_exclusive", True)


def test_a_manual_free_makes_the_queue_forget_who_held_the_card(app_modules):
    """Otherwise the next job trusts a stale picture and skips an eviction it
    still needed."""
    vram = app_modules["vram"]
    _run(vram, "image.generate", [])
    assert vram.resident() == [vram.COMFY]
    vram.forget()
    assert vram.resident() is None
    evicted = []
    _run(vram, "image.generate", evicted)
    assert evicted == [vram.LM]


def test_an_llm_job_reloads_the_model_when_nothing_is_resident(app_modules):
    """Unloading LM Studio is one-way: it only JIT-loads when the request
    names a model, and a blank prose model here means "whatever is loaded".
    So an LLM job has to check residency, not just ownership of the card."""
    vram = app_modules["vram"]
    loads = []
    swap = _run(vram, "image.cg_prompt", [], llm_loaded=False, loads=loads)
    assert loads == ["restore"], "an LLM job must load a model if none is resident"
    assert swap["swapped"] is True
    assert any("loaded" in n for n in swap["notes"])


def test_an_llm_job_reloads_even_when_it_already_owns_the_card(app_modules):
    """Residency means "owns the card", not "has a model in it" -- the model
    can go away under us via a TTL, or because we unloaded it two jobs ago."""
    vram = app_modules["vram"]
    _run(vram, "llm.summarize", [])                 # LM now owns the card
    loads = []
    swap = _run(vram, "llm.summarize", [], llm_loaded=False, loads=loads)
    assert loads == ["restore"]
    assert swap["swapped"] is True


def test_an_llm_job_with_a_model_already_loaded_does_nothing(app_modules):
    vram = app_modules["vram"]
    _run(vram, "llm.summarize", [])
    loads, evicted = [], []
    swap = _run(vram, "llm.summarize", evicted, llm_loaded=True, loads=loads)
    assert swap["swapped"] is False
    assert loads == [] and evicted == []


def test_evicting_the_llm_writes_down_what_to_put_back(app_modules):
    """The record has to be taken before the unload, or there is nothing left
    to read it off -- and it is persisted so a restart mid-batch still knows."""
    vram, store = app_modules["vram"], app_modules["store"]
    store.set_setting("lm_studio_last_loaded", [])

    async def fake_loaded():
        return [{"id": "cydonia-24b", "context": 29110}]

    async def fake_unload():
        # Whatever we recorded must already be in place by now.
        assert store.get_setting("lm_studio_last_loaded", []), \
            "the resident model must be recorded before it is unloaded"
        return True, "unloaded the LLM"

    real = (vram.lmstudio.loaded, vram.lmstudio.unload)
    vram.lmstudio.loaded, vram.lmstudio.unload = fake_loaded, fake_unload
    try:
        ok, note = asyncio.run(vram._evict(vram.LM))
    finally:
        vram.lmstudio.loaded, vram.lmstudio.unload = real

    assert ok and "unloaded" in note
    remembered = store.get_setting("lm_studio_last_loaded", [])
    assert remembered == [{"id": "cydonia-24b", "context": 29110}]


def test_restore_prefers_what_was_resident_over_the_configured_model(app_modules):
    """Blank model settings mean "whatever is loaded", so the remembered model
    is the more faithful answer -- and it carries the context length it was
    actually running at, which the model's own default would not."""
    vram, store = app_modules["vram"], app_modules["store"]
    store.set_setting("lm_studio_last_loaded", [{"id": "cydonia-24b", "context": 29110}])
    store.set_setting("lm_studio_prose_model", "some-other-model")

    calls = []

    async def fake_loaded_models():
        return []

    async def fake_load(model_key, context=0):
        calls.append((model_key, context))
        return True, f"loaded {model_key}"

    real = (vram.lmstudio.loaded_models, vram.lmstudio.load)
    vram.lmstudio.loaded_models, vram.lmstudio.load = fake_loaded_models, fake_load
    try:
        ok, note = asyncio.run(vram._restore_llm())
    finally:
        vram.lmstudio.loaded_models, vram.lmstudio.load = real
        store.set_setting("lm_studio_prose_model", "")

    assert ok, note
    assert calls == [("cydonia-24b", 29110)]


def test_restore_falls_back_to_the_configured_model(app_modules):
    vram, store = app_modules["vram"], app_modules["store"]
    store.set_setting("lm_studio_last_loaded", [])
    store.set_setting("lm_studio_prose_model", "configured-model")

    calls = []

    async def fake_loaded_models():
        return []

    async def fake_load(model_key, context=0):
        calls.append((model_key, context))
        return True, f"loaded {model_key}"

    real = (vram.lmstudio.loaded_models, vram.lmstudio.load)
    vram.lmstudio.loaded_models, vram.lmstudio.load = fake_loaded_models, fake_load
    try:
        ok, _ = asyncio.run(vram._restore_llm())
    finally:
        vram.lmstudio.loaded_models, vram.lmstudio.load = real
        store.set_setting("lm_studio_prose_model", "")

    assert ok
    assert calls == [("configured-model", 0)]


def test_restore_says_so_when_there_is_nothing_to_load(app_modules):
    """Better a job that fails with a readable reason than one that fails with
    LM Studio's 400."""
    vram, store = app_modules["vram"], app_modules["store"]
    store.set_setting("lm_studio_last_loaded", [])
    store.set_setting("lm_studio_prose_model", "")
    store.set_setting("lm_studio_struct_model", "")

    async def fake_loaded_models():
        return []

    real = vram.lmstudio.loaded_models
    vram.lmstudio.loaded_models = fake_loaded_models
    try:
        ok, note = asyncio.run(vram._restore_llm())
    finally:
        vram.lmstudio.loaded_models = real

    assert ok is False
    assert "nothing to reload" in note


def test_fill_missing_puts_all_the_llm_work_first(app_modules):
    """One swap for an overnight batch instead of three. Direction reads the
    script, not the rendered assets, so moving it to the front costs nothing."""
    vram = app_modules["vram"]
    source = Path("app/jobs/handlers.py").read_text(encoding="utf-8")
    body = source.split('@handler("batch.fill_missing")')[1]

    order = []
    for kind in ("cg_prompts", "backgrounds", "sprites", "cgs", "voice"):
        marker = f'report["{kind}"]'
        assert marker in body, kind
        order.append((body.index(marker), kind))
    order.sort()
    queued_order = [kind for _, kind in order]
    assert queued_order[0] == "cg_prompts", queued_order

    # Which is only worth anything if that block really is the LLM one.
    assert vram.engine_for("image.cg_prompt") == vram.LM
    for kind in ("image.background", "image.sprite", "image.generate", "tts.line"):
        assert vram.engine_for(kind) == vram.COMFY
