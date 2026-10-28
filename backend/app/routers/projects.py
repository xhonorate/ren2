"""Projects, settings, service health, wizard vocabulary."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..clients import comfyui, lmstudio
from ..defaults import (
    CG_SET_ACTIONS,
    CG_SET_COMPANY,
    CG_VOCAB,
    DEFAULT_SETTINGS,
    EMOTION_DIMS,
    EMOTION_MAP,
    SHEET_FRAMINGS,
    SHOT_VOCAB,
    TAG_VOCAB,
    TIME_OF_DAY,
    WEATHER_TAGS,
)
from ..graph import collect_flags, stat_variable_infos
from ..jobs.queue import enqueue
from ..models import (
    CG_LORA_MODES,
    EMOTIONS,
    EXPRESSIONS,
    POSES,
    TRANSITIONS,
    ProjectMeta,
    StatDef,
    VariableDef,
    WorldBible,
)
from ..services import assets as asset_svc, tags as tag_svc, vram

router = APIRouter()


class CreateProject(BaseModel):
    name: str


class UpdateProject(BaseModel):
    name: Optional[str] = None
    world: Optional[WorldBible] = None
    variables: Optional[list[VariableDef]] = None
    character_stats: Optional[list[StatDef]] = None
    style_tags: Optional[str] = None
    quality_prefix: Optional[str] = None
    global_negative: Optional[str] = None


@router.get("/projects")
def list_projects() -> list[ProjectMeta]:
    return store.list_projects()


@router.post("/projects")
def create_project(body: CreateProject) -> ProjectMeta:
    return store.create_project(body.name.strip() or "Untitled")


@router.get("/projects/{project_id}")
def get_project(project_id: str) -> ProjectMeta:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: UpdateProject) -> ProjectMeta:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    for field in body.model_dump(exclude_none=True):
        # The parsed value, not the dumped one: model_dump turns
        # `character_stats` and `variables` into plain dicts, and assignment
        # does not re-validate, so the project would end up holding dicts where
        # it declares models -- which pydantic warns about on the way back out.
        value = getattr(body, field)
        if field == "world":
            # Merge, don't replace: a client that PATCHes {"world": {"setting": x}}
            # only means to change `setting`. model_dump on the parsed WorldBible
            # would carry every other field at its default and wipe them; take
            # just what the client actually sent. model_copy also keeps `world`
            # a real WorldBible (a raw dict here trips a serialization warning).
            sent = body.world.model_dump(exclude_unset=True) if body.world else {}
            project.world = project.world.model_copy(update=sent)
        else:
            setattr(project, field, value)
    return store.save_project(project)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, bool]:
    store.delete_project(project_id)
    return {"deleted": True}


# --------------------------------------------------------------------------
# story variables (dropdown source for Set / Condition / Choice editors)
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/variables")
def list_variables(project_id: str) -> dict[str, Any]:
    """The declared variable list, merged with any name that shows up in a Set
    node or a choice effect but was never saved (`derived: true`)."""
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    declared = {v.name: v for v in project.variables}
    used = collect_flags(store.list_nodes(project_id, include_orphaned=True))
    # Stat instances are never typed by anyone -- the schema is declared once
    # and the `{stat}_{character}` variables it implies only exist at compile
    # time -- so without this they would be missing from every picker that
    # gates on them. A hand-declared variable of the same name still wins,
    # matching graph.merged_flag_defaults.
    stat_vars = [s for s in stat_variable_infos(
        project, store.list_characters(project_id)) if s["name"] not in declared]

    out: list[dict[str, Any]] = []
    for v in project.variables:
        out.append({**v.model_dump(), "derived": False})
    out.extend(stat_vars)
    stat_names = {s["name"] for s in stat_vars}
    for name, sample in sorted(used.items()):
        if name in declared or name in stat_names:
            continue
        vtype = "bool" if isinstance(sample, bool) else (
            "int" if isinstance(sample, (int, float)) else "str")
        out.append({"name": name, "type": vtype, "default": "", "note": "",
                    "derived": True})
    return {"variables": out}


@router.put("/projects/{project_id}/variables")
def put_variables(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    raw = body.get("variables", body if isinstance(body, list) else [])
    seen: set[str] = set()
    cleaned: list[VariableDef] = []
    for item in raw:
        v = VariableDef.model_validate(item)
        v.name = v.name.strip()
        if not v.name or v.name in seen:
            continue
        seen.add(v.name)
        cleaned.append(v)
    project.variables = cleaned
    store.save_project(project)
    return {"variables": [v.model_dump() for v in cleaned]}


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

@router.get("/settings")
def get_settings() -> dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    merged.update(store.all_settings())
    return merged


@router.put("/settings")
def put_settings(body: dict[str, Any]) -> dict[str, Any]:
    for key, value in body.items():
        store.set_setting(key, value)
    return get_settings()


@router.get("/health/services")
async def service_health() -> dict[str, Any]:
    lm = await lmstudio.health()
    comfy = await comfyui.health()
    checkpoints = await comfyui.available("checkpoints") if comfy["ok"] else []
    loras = await comfyui.available("loras") if comfy["ok"] else []
    samplers = await comfyui.available("samplers") if comfy["ok"] else []
    schedulers = await comfyui.available("schedulers") if comfy["ok"] else []
    return {
        "lm_studio": lm,
        "comfyui": comfy,
        "checkpoints": checkpoints,
        "loras": loras,
        "samplers": samplers,
        "schedulers": schedulers,
        "assets": asset_svc.usage_report(),
    }


@router.get("/llm/models")
async def llm_models() -> dict[str, Any]:
    """What LM Studio currently has available, for the model dropdowns.

    Separate from /health/services so the Config page can fill three selects on
    mount without also sweeping ComfyUI for checkpoints, LoRAs and samplers.
    Never raises: an unreachable server means an empty list and a reason, and
    the fields fall back to free text.
    """
    try:
        models = await lmstudio.list_models()
    except lmstudio.LMStudioError as exc:
        return {"ok": False, "models": [], "error": str(exc)}
    return {"ok": True, "models": [m.get("id") for m in models if m.get("id")]}


@router.post("/comfyui/free")
async def free_vram() -> dict[str, bool]:
    """VRAM recovery -- one GPU cannot hold an LLM, SDXL and TTS at once."""
    await comfyui.free()
    # The queue tracks who holds the card so it can skip needless swaps; a
    # manual free from outside makes that picture stale.
    vram.forget()
    return {"freed": True}


@router.post("/llm/unload")
async def unload_llm() -> dict[str, Any]:
    """Drop LM Studio's resident models, freeing their VRAM for ComfyUI.

    The manual twin of what a job swap does automatically, and the quickest
    way to find out whether `lms` is reachable at all.
    """
    ok, note = await lmstudio.unload()
    vram.forget()
    return {"unloaded": ok, "note": note}


@router.get("/vram")
async def vram_status() -> dict[str, Any]:
    """Who the queue thinks holds the card, and whether it can swap at all."""
    return {
        "exclusive": vram.enabled(),
        "resident": vram.resident(),
        "can_unload_llm": bool(lmstudio.cli()),
        "engines": vram.ENGINE_BY_KIND,
    }


@router.post("/comfyui/interrupt")
async def interrupt() -> dict[str, bool]:
    await comfyui.interrupt()
    return {"interrupted": True}


# --------------------------------------------------------------------------
# booru tag dictionary (autocomplete for anything that reaches ComfyUI)
# --------------------------------------------------------------------------

@router.get("/tags/search")
def tag_search(q: str, limit: int = 12, category: str = "",
               prefer: str = "") -> dict[str, Any]:
    """Rank tags for a partially typed token.

    Server-side rather than shipping the corpus to the browser: it is ~30k
    rows after an import, and the ranking wants the post counts, so sending
    the whole thing on every page load to re-rank it in JS would be worse on
    both counts.
    """
    cats = [c.strip() for c in category.split(",") if c.strip()]
    wants = [c.strip() for c in prefer.split(",") if c.strip()]
    return {"results": tag_svc.search(q, limit=max(1, min(50, limit)),
                                      categories=cats, prefer=wants)}


@router.get("/tags/check")
def tag_check(tags: str = "") -> dict[str, Any]:
    """Which of these tags the dictionary does not know, and the likely fix.

    The screen that `generation.clean_cg_tags` applies to director output, made
    available to the fields it never covered: a tag typed into a character or
    location record reaches ComfyUI verbatim, and a misspelled one conditions
    on nothing rather than failing.
    """
    names = [t.strip() for t in tags.split(",") if t.strip()]
    return {"unknown": tag_svc.check(names)}


@router.get("/tags/status")
def tag_status() -> dict[str, Any]:
    """How much of a dictionary this install has, and where it came from."""
    return tag_svc.status()


class TagImport(BaseModel):
    # Below ~100 posts a tag is in the training data as a handful of images,
    # which the checkpoint has not really learned -- offering it would make
    # the list longer and the suggestions worse.
    min_count: int = tag_svc.DEFAULT_MIN_COUNT


@router.post("/tags/import")
def tag_import(body: TagImport | None = None) -> dict[str, str]:
    """Fetch the tag list from Danbooru. Not scoped to a project -- the
    dictionary is a property of the install, like the checkpoint."""
    payload = (body or TagImport()).model_dump()
    return {"job_id": enqueue("", "tags.import", payload,
                              label="import booru tags")}


# --------------------------------------------------------------------------
# vocabulary the wizard and inspectors read
# --------------------------------------------------------------------------

@router.get("/vocab")
def vocab() -> dict[str, Any]:
    return {
        "tags": TAG_VOCAB,
        "emotions": EMOTIONS,
        "expressions": EXPRESSIONS,
        "poses": POSES,
        "transitions": TRANSITIONS,
        "shots": SHOT_VOCAB,
        "cg_tags": CG_VOCAB,
        "cg_lora_modes": CG_LORA_MODES,
        # The CG training set's own axes -- what the body is doing, who else
        # is in frame, how close. Offered as chips on the CG LoRA tab.
        "cg_set_actions": CG_SET_ACTIONS,
        "cg_set_company": CG_SET_COMPANY,
        "cg_set_framings": SHEET_FRAMINGS,
        "time_of_day": TIME_OF_DAY,
        "weather": list(WEATHER_TAGS),
        "emotion_dims": EMOTION_DIMS,
        "emotion_map": EMOTION_MAP,
    }
