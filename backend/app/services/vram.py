"""Which engine owns the card right now, and evicting the one that does not.

No single-GPU machine can hold a 24B LLM and SDXL at the same time, and both
LM Studio and ComfyUI grab dedicated VRAM and keep it. The queue already
serialises GPU work behind a single lock (`jobs.queue`), which makes the moment
a job acquires that lock the one place in the system where it is safe to unload
somebody else's model: nothing is mid-run, and nothing else can start.

**This module used to claim "one 24 GB GPU" throughout, and that was never
measured.** The development machine is a 16 GB RTX 5080 plus an 8 GB RTX 3070,
and LM Studio splits a 24B across both of them (measured: 13.9 GB on the 5080,
6.3 GB on the 3070 for Magidonia 24B Q4_K_M at 16k context). Two cards do not
make the problem go away, because the big image model has to be on the big card
and a split LLM is already sitting on it. See `gpu_report` for the live numbers
and `services/roleplay` for what that costs real-time voice.

So this is a residency tracker, not a scheduler. `ensure` is called inside the
lock with the kind of job about to run; if a different engine is holding the
card, it is told to let go first.

A swap is two-way, and has to be. Unloading LM Studio is not reversible on its
own: it only JIT-loads a model when the request names one, and a blank
`lm_studio_prose_model` here means "whatever is loaded" -- so an unload under
the default settings leaves the next writing job with a 400 and nothing to
load. `_remember_llm` writes down what was resident before evicting it and
`_restore_llm` puts it back, at the context length it was running at.

The tax is reload time -- a 24B measured at ~5s off an NVMe, longer off a cold
disk -- so the goal is few swaps, not fast ones. `batch.fill_missing` queues
all of its LLM work in one contiguous block ahead of the renders for exactly
this reason: one swap for a whole overnight batch instead of one per node.

TTS is deliberately not its own engine: IndexTTS-2.5 runs *through* ComfyUI
(services.tts), so freeing ComfyUI frees the voice model with it.
"""
from __future__ import annotations

import asyncio
import subprocess
from typing import Any, Awaitable, Callable, Optional

from .. import store
from ..clients import comfyui, lmstudio

# The engines that hold VRAM.
LM = "lm_studio"          # LM Studio: the prose and structuring models
COMFY = "comfyui"         # ComfyUI: SDXL, the face detailer, IndexTTS-2.5
TRAINER = "trainer"       # sd-scripts, its own process, wants the whole card
UNKNOWN = "unknown"       # a GPU kind nobody mapped -- assume it wants everything

# Everything that can actually be asked to unload. TRAINER is absent because
# its process has exited by the time anything else runs.
EVICTABLE = (LM, COMFY)

# Stated per job kind rather than by prefix, because the prefixes lie:
# `image.cg_prompt` is the CG director, which is an LM Studio call with no
# ComfyUI in it at all, while `image.caption` is a WD14 tagger node and is pure
# ComfyUI. A prefix map would have the director evict the very model it is
# about to use. Add new GPU kinds here; an unlisted one still works, it just
# pays for a full eviction (see `engine_for`).
ENGINE_BY_KIND: dict[str, str] = {
    "llm.generate_scene": LM,
    "llm.regenerate_from_node": LM,
    "llm.summarize": LM,
    "image.cg_prompt": LM,
    "rp.reply": LM,
    "rp.summarize": LM,
    "rp.seed": LM,
    "rp.port": LM,

    "image.background": COMFY,
    "image.generate": COMFY,
    "image.refine": COMFY,
    "image.sprite": COMFY,
    "image.profile": COMFY,
    "image.sheet": COMFY,
    "image.cg_set": COMFY,
    "image.caption": COMFY,
    "tts.line": COMFY,
    "tts.cast": COMFY,

    "lora.train": TRAINER,
}

# What each engine is called in a job label.
LABELS = {LM: "the LLM", COMFY: "ComfyUI", TRAINER: "LoRA training",
          UNKNOWN: "this job"}

# What we believe is loaded. `None` means we do not know yet -- the user may
# have both apps open with models in memory, which is the situation this module
# exists for -- so the first job of a session sweeps everything it can.
_resident: Optional[set[str]] = None
_lock = asyncio.Lock()


def enabled() -> bool:
    return bool(store.get_setting("vram_exclusive", True))


# --------------------------------------------------------------------------
# what the cards actually have
# --------------------------------------------------------------------------

def gpu_report() -> list[dict[str, int]]:
    """Per-GPU total / used / free, in MiB. Empty list if nvidia-smi is absent.

    Read from nvidia-smi rather than from torch because nothing in this process
    needs torch and importing it to ask one question would cost seconds and a
    CUDA context. Never raises: this feeds an advisory panel, and a machine with
    no NVIDIA driver should see "unknown", not a traceback.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,name,memory.total,memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []

    cards: list[dict[str, int]] = []
    for line in out.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            total, used = int(parts[2]), int(parts[3])
        except ValueError:
            continue
        cards.append({"index": int(parts[0]), "name": parts[1], "total_mb": total,
                      "used_mb": used, "free_mb": max(0, total - used)})
    return cards


def roleplay_voice_fit() -> dict[str, Any]:
    """Whether the voice model can sit alongside the LLM for a roleplay session.

    Real-time voice needs IndexTTS-2.5 resident for the whole session -- loading it
    per line costs about twelve seconds, which is not real-time by any reading. So
    the question is simply whether its footprint fits in what the LLM has left
    over, across all cards, and that is worth answering *before* a session rather
    than by running out of memory in the middle of one.

    The voice figure is a setting rather than a constant because it is a property
    of the machine and the workflow, not of this code. The default is measured:
    see `rp_tts_vram_mb`.
    """
    cards = gpu_report()
    need = int(store.get_setting("rp_tts_vram_mb", 6500) or 6500)
    if not cards:
        return {"known": False, "needed_mb": need,
                "note": "nvidia-smi is not available, so VRAM cannot be checked"}

    free = sum(c["free_mb"] for c in cards)
    # The voice model is one model: it has to fit on a single card, not across the
    # sum of them. ComfyUI places it on one device and does not shard it.
    biggest = max(c["free_mb"] for c in cards)
    fits = biggest >= need
    return {
        "known": True,
        "cards": cards,
        "needed_mb": need,
        "free_total_mb": free,
        "free_largest_card_mb": biggest,
        "fits": fits,
        "note": ("the voice model fits alongside what is loaded now"
                 if fits else
                 f"the voice model needs {need} MiB on one card and the most any "
                 f"card has free is {biggest} MiB -- load the LLM at a shorter "
                 f"context or a smaller quantisation, or turn real-time voice off"),
    }


def engine_for(kind: str) -> str:
    """The engine a job kind needs, or UNKNOWN.

    UNKNOWN is the fail-safe direction on purpose: an unmapped kind evicts
    everything and reloads what it needs, which costs one model load. Guessing
    the other way -- assuming it needs nothing -- is an out-of-memory error
    partway through a job that has already spent GPU time on the card.
    """
    return ENGINE_BY_KIND.get(kind, UNKNOWN)


def resident() -> Optional[list[str]]:
    return None if _resident is None else sorted(_resident)


def forget() -> None:
    """Drop what we think we know about the card.

    Called when something outside the queue changes it -- the Config page's
    "Free VRAM" button, or a manual unload. Without this the next job would
    trust a stale picture and skip an eviction it still needed.
    """
    global _resident
    _resident = None


async def _remember_llm() -> None:
    """Record what LM Studio has resident, so a swap can put it back.

    Necessary because unloading is one-way here: LM Studio only JIT-loads when
    the request names a model, and this project's convention is that a blank
    `lm_studio_prose_model` means "whatever is loaded". Unload under those
    settings and the next writing job gets a 400 with nothing to reload -- so
    what was there has to be written down before it goes.

    Persisted to settings rather than kept in memory: a restart in the middle
    of an overnight batch would otherwise forget how to bring the LLM back.
    """
    models = await lmstudio.loaded()
    if models:
        store.set_setting("lm_studio_last_loaded", models)


async def _restore_llm() -> tuple[bool, str]:
    """Make sure some LLM is actually resident, loading one if not.

    Prefers exactly what was there when we evicted -- that honours the blank
    "whatever is loaded" setting -- and falls back to the configured prose or
    structuring model when there is no history to go on.
    """
    if await lmstudio.loaded_models():
        return True, ""

    remembered = store.get_setting("lm_studio_last_loaded", []) or []
    targets = [(m.get("id", ""), m.get("context") or 0)
               for m in remembered if isinstance(m, dict) and m.get("id")]
    if not targets:
        configured = (store.get_setting("lm_studio_prose_model", "")
                      or store.get_setting("lm_studio_struct_model", ""))
        if configured:
            targets = [(str(configured), 0)]

    if not targets:
        return False, ("nothing to reload: no model was seen resident and no "
                       "prose model is configured -- load one in LM Studio")

    notes, failures = [], []
    for model_key, context in targets:
        ok, note = await lmstudio.load(model_key, context)
        (notes if ok else failures).append(note)
    if failures:
        return False, "; ".join(failures)
    return True, "; ".join(n for n in notes if n)


async def _evict(engine: str) -> tuple[bool, str]:
    """Tell one engine to drop its models.

    Never raises. A failed eviction should cost a slow job or an out-of-memory
    error the user can read, not a dead queue -- and LM Studio in particular
    can fail for a mundane reason (no `lms` on PATH) that must not stop image
    work that would have been fine anyway.
    """
    if engine == COMFY:
        await comfyui.free(unload_models=True, free_memory=True)
        return True, "unloaded ComfyUI's models"
    if engine == LM:
        await _remember_llm()
        return await lmstudio.unload()
    return True, ""


async def ensure(kind: str,
                 notify: Optional[Callable[[str], Awaitable[None]]] = None) -> dict:
    """Make the card available to `kind`, evicting whoever else holds it.

    Call inside the queue's GPU lock. Returns what it did, so a swap shows up
    in the job result rather than as mysterious dead time at the start of a job.
    """
    global _resident

    engine = engine_for(kind)
    if not enabled():
        return {"engine": engine, "swapped": False, "reason": "vram_exclusive is off"}

    async with _lock:
        believed = set(EVICTABLE) if _resident is None else set(_resident)
        # UNKNOWN keeps nothing: it is unmapped, so we cannot say what it needs.
        keep: set[str] = set() if engine == UNKNOWN else {engine}
        targets = sorted(believed - keep)

        notes: list[str] = []
        failures: list[str] = []

        if targets:
            if notify is not None:
                await notify(f"freeing VRAM for {LABELS[engine]}")
            for target in targets:
                ok, note = await _evict(target)
                if not note:
                    continue
                if ok:
                    notes.append(note)
                else:
                    failures.append(f"could not free {LABELS[target]}: {note}")

        # An LLM job needs the model *loaded*, which is not the same as "not
        # evicted": we may have unloaded it ourselves two jobs ago, or its TTL
        # may have expired. Checked even on the already-resident path, because
        # residency here means "owns the card", not "has a model in it".
        if engine == LM:
            if await lmstudio.loaded_models():
                pass
            else:
                if notify is not None:
                    await notify("loading the LLM")
                ok, note = await _restore_llm()
                if note:
                    (notes if ok else failures).append(note)

        if not notes and not failures:
            _resident = keep
            return {"engine": engine, "swapped": False, "reason": "already resident"}

        # Recorded as held even when an eviction failed: the job is about to
        # run either way, and claiming the loser is still resident would make
        # every subsequent job retry the same failing unload.
        _resident = keep
        return {"engine": engine, "swapped": True,
                "notes": notes, "failures": failures}
