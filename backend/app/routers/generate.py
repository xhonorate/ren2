"""Generation endpoints -- everything here enqueues a job and returns its id."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..graph import Graph
from ..jobs.queue import enqueue
from ..models import NodeType
from ..services import context as ctx_mod, generation, images, staleness, tts

router = APIRouter()


class GenerateScene(BaseModel):
    anchor_id: str
    handle: str = "out"
    brief: str = ""
    branch_hint: str = ""
    instruction: str = ""       # one-off steer: "shorter", "angrier", ...
    # One of the directions the previous generation offered, picked by the
    # author. Distinct from `instruction`: it says where the scene goes, not
    # how to write it, and is rendered as such.
    continuation: str = ""
    materialize: bool = True


@router.post("/projects/{project_id}/generate/scene")
def generate_scene(project_id: str, body: GenerateScene) -> dict[str, str]:
    if store.get_node(body.anchor_id) is None:
        raise HTTPException(404, "anchor node not found")
    job = enqueue(project_id, "llm.generate_scene", body.model_dump(),
                  label="generate scene", node_id=body.anchor_id)
    return {"job_id": job}


class GenerateBranch(BaseModel):
    choice_id: str
    option_id: str
    brief: str = ""
    instruction: str = ""


@router.post("/projects/{project_id}/generate/branch")
def generate_branch(project_id: str, body: GenerateBranch) -> dict[str, str]:
    """Generate down one choice option. Context follows that branch only.

    Hubs take the same path: a hub option is a choice option with structured
    gates, and generating a topic is generating a branch.
    """
    node = store.get_node(body.choice_id)
    if node is None or node.type not in (NodeType.choice, NodeType.hub):
        raise HTTPException(404, "choice node not found")
    option = next((o for o in (node.data or {}).get("options", [])
                   if o.get("id") == body.option_id), None)
    if option is None:
        raise HTTPException(404, "option not found")
    job = enqueue(project_id, "llm.generate_scene", {
        "anchor_id": body.choice_id,
        "handle": body.option_id,
        "brief": body.brief,
        "branch_hint": option.get("branch_hint") or option.get("label", ""),
        "instruction": body.instruction,
    }, label=f"branch: {option.get('label', '')[:24]}", node_id=body.choice_id)
    return {"job_id": job}


class RegenerateFrom(BaseModel):
    node_id: str
    brief: str = ""
    instruction: str = ""
    # Lets a continuation suggestion be re-picked on a node that has already
    # been continued past: appending would splice a second chain in front of
    # existing content, where what the author means is "try that other
    # direction instead". The payload flows straight through to
    # llm.generate_scene, which reads it the same way.
    continuation: str = ""


@router.post("/projects/{project_id}/generate/regenerate-from")
def regenerate_from(project_id: str, body: RegenerateFrom) -> dict[str, str]:
    label = f"regenerate: {body.instruction[:24]}" if body.instruction else "regenerate from node"
    job = enqueue(project_id, "llm.regenerate_from_node", body.model_dump(),
                  label=label, node_id=body.node_id)
    return {"job_id": job}


class RegenerateBlock(BaseModel):
    node_id: str                 # any node of the generation to redo
    brief: str = ""
    instruction: str = ""


@router.post("/projects/{project_id}/generate/regenerate-block")
def regenerate_block(project_id: str, body: RegenerateBlock) -> dict[str, str]:
    """Redo the whole generation a node belongs to.

    Distinct from regenerate-from, which starts at the selected node: if the
    first line of a six-node passage is the problem, regenerating from the
    node you happen to have clicked redoes only the tail. This walks back to
    where that generation began.
    """
    node = store.get_node(body.node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    gen_id = (node.data or {}).get("_gen")
    if not gen_id:
        raise HTTPException(422, "this node was not produced by a generation -- "
                                 "use regenerate-from instead")
    head = generation.generation_head(project_id, gen_id)
    if head is None:
        raise HTTPException(404, "that generation's nodes are no longer in the graph")
    job = enqueue(project_id, "llm.regenerate_from_node",
                  {"node_id": head, "brief": body.brief,
                   "instruction": body.instruction},
                  label="regenerate block", node_id=head)
    return {"job_id": job, "head": head}


class ParkAfter(BaseModel):
    node_id: str
    include_self: bool = False


@router.post("/projects/{project_id}/generate/park-after")
def park_after(project_id: str, body: ParkAfter) -> dict[str, Any]:
    """Move everything downstream of a node to the holding area.

    This is rewind. Nothing is deleted -- parked nodes come back through
    /orphans/restore, which is what makes trying a different direction in
    improv safe rather than destructive. A merge point still fed by another
    live branch stays put (services/generation.orphan_subtree), so rewinding
    one path never tears out a later scene both paths reach.
    """
    if store.get_node(body.node_id) is None:
        raise HTTPException(404, "node not found")
    parked = generation.orphan_subtree(project_id, body.node_id,
                                       include_self=body.include_self)
    return {"parked": parked, "count": len(parked)}


@router.post("/projects/{project_id}/generate/discard/{gen_id}")
def discard_generation(project_id: str, gen_id: str) -> dict[str, Any]:
    """Regenerate scene: drop the whole generated block."""
    removed = generation.discard_generation(project_id, gen_id)
    return {"removed": removed, "count": len(removed)}


@router.post("/projects/{project_id}/generate/rollback")
def rollback_generation(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Undo a materialized generation: park its nodes, restore what it replaced."""
    if not body.get("anchor_id"):
        raise HTTPException(400, "rollback needs the generation descriptor")
    return generation.rollback_generation(project_id, body)


@router.post("/projects/{project_id}/generate/reapply")
def reapply_generation(project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Redo: the exact inverse of /generate/rollback."""
    if not body.get("anchor_id"):
        raise HTTPException(400, "reapply needs the generation descriptor")
    return generation.reapply_generation(project_id, body)


@router.get("/projects/{project_id}/context-preview")
async def context_preview(project_id: str, node_id: str, brief: str = "",
                          instruction: str = "", continuation: str = "",
                          branch_hint: str = "") -> dict[str, Any]:
    """The exact layered prompt that would be sent, without sending it.

    The three steering inputs are accepted because they are the whole of the
    user turn -- a preview that could not take them showed a user block the
    real generation never sends, which is the opposite of what this is for.
    """
    graph = Graph.build(store.list_nodes(project_id, include_orphaned=False),
                        store.list_edges(project_id))
    if node_id not in graph.nodes:
        raise HTTPException(404, "node not found")
    assembled = await ctx_mod.assemble(
        project_id, graph, node_id, brief=brief, branch_hint=branch_hint,
        extra_instruction=instruction, continuation=continuation, summarize=False)
    return {
        "system": assembled.system,
        "user": assembled.user,
        "state": assembled.state.to_dict(),
        "present": assembled.present,
        "recent": assembled.recent,
        "token_estimate": assembled.token_estimate,
    }


@router.post("/projects/{project_id}/summarize")
def summarize(project_id: str, body: dict[str, str]) -> dict[str, str]:
    job = enqueue(project_id, "llm.summarize", {"node_id": body["node_id"]},
                  label="summarize path", node_id=body["node_id"])
    return {"job_id": job}


# --------------------------------------------------------------------------
# variants
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/nodes/{node_id}/variants")
def list_variants(project_id: str, node_id: str) -> dict[str, Any]:
    node = store.get_node(node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    return {"active": node.active_variant,
            "variants": [v.model_dump() if hasattr(v, "model_dump") else v
                         for v in node.variants]}


@router.post("/projects/{project_id}/nodes/{node_id}/variants/{variant_id}/apply")
def apply_variant(project_id: str, node_id: str, variant_id: str) -> dict[str, Any]:
    node = store.get_node(node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    try:
        updated = generation.apply_variant(project_id, node, variant_id)
    except generation.GenerationError as exc:
        raise HTTPException(404, str(exc)) from exc
    staleness.evaluate(updated, staleness.voice_refs(project_id))
    store.update_node(project_id, updated)
    return updated.model_dump()


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

class ImageRequest(BaseModel):
    node_id: str
    seed: Optional[int] = None
    shot_hint: str = ""
    # One-off overrides of the node's distilled prompt. Empty means "use what
    # is on the node", which is the normal path -- the inspector edits the node
    # fields directly rather than passing them here.
    camera: str = ""
    action: str = ""
    negative: str = ""
    lora: str = ""               # "" | "auto" | "none" | "sprite" | "cg"; "" = project default
    # One-off cast override. None means "use the node's cg_subjects, or work it
    # out from who is on stage"; a list is taken literally.
    subjects: Optional[list[str]] = None
    face_detail: Optional[bool] = None
    distill_if_missing: bool = False
    width: Optional[int] = None
    height: Optional[int] = None
    # How many candidates to sample in one run, kept as takes on the node so
    # the author picks one. 1 (the default) is the old behaviour; the
    # unattended batch paths never raise it.
    batch: Optional[int] = None


@router.post("/projects/{project_id}/images/cg")
def generate_cg(project_id: str, body: ImageRequest) -> dict[str, str]:
    return {"job_id": enqueue(project_id, "image.generate", body.model_dump(),
                              label="event CG", node_id=body.node_id)}


class RefineRequest(BaseModel):
    node_id: str
    # Which take to start from. Empty means the node's current image, so the
    # ordinary "refine what I'm looking at" click sends nothing.
    source_hash: str = ""
    # How far the sampler may move from the source. The inspector shows the
    # inverse of this as "adherence", which is how an author thinks about it.
    denoise: float = 0.45
    # A black-and-white PNG data URL: white is the region to regenerate.
    # Absent means the whole frame.
    mask: str = ""
    batch: int = 1
    seed: Optional[int] = None


@router.post("/projects/{project_id}/images/refine")
def refine_image(project_id: str, body: RefineRequest) -> dict[str, str]:
    """Re-sample an existing take, optionally only inside a painted region.

    For the CG whose composition, lighting and expression are all right and
    whose left hand is wrong: re-rolling throws away the four good things to
    fix the one bad one.
    """
    node = store.get_node(body.node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    if not (body.source_hash or (node.data or {}).get("image_hash")):
        raise HTTPException(400, "nothing to refine: this node has no image yet")
    return {"job_id": enqueue(project_id, "image.refine", body.model_dump(),
                              label="refine CG", node_id=body.node_id)}


class CgPromptRequest(BaseModel):
    node_id: str
    instruction: str = ""        # one-off steer for the director
    # Leave a node that already has a shot alone. Off for a click in the
    # inspector -- re-distilling is exactly what that button is for -- and on
    # for the jobs batch.fill_missing queues, where the render behind may
    # have got there first.
    skip_if_set: bool = False


@router.post("/projects/{project_id}/images/cg/prompt")
def distill_cg_prompt(project_id: str, body: CgPromptRequest) -> dict[str, str]:
    """Run the CG director over one node: distills its context into image tags
    and writes them onto the node, where the inspector shows them for editing."""
    if store.get_node(body.node_id) is None:
        raise HTTPException(404, "node not found")
    return {"job_id": enqueue(project_id, "image.cg_prompt", body.model_dump(),
                              label="distill CG prompt", node_id=body.node_id)}


@router.get("/projects/{project_id}/images/cg/preview")
def preview_cg_prompt(project_id: str, node_id: str) -> dict[str, Any]:
    """The exact positive/negative this node would send to ComfyUI right now.

    Assembled by the same code the render job uses (`handlers.cg_inputs`), so
    what the inspector shows is what gets generated -- a preview built by a
    parallel code path drifts and is then worse than none.
    """
    from ..jobs.handlers import cg_inputs   # local: handlers imports routers' siblings

    node = store.get_node(node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    resolved = cg_inputs(project_id, node)
    return {
        "positive": resolved["prompt"]["positive"],
        "negative": resolved["prompt"]["negative"],
        "subjects": [c.id for c in resolved["subjects"]],
        # Every tag the cast contributes to *this* shot, per character, before
        # the node's switches are applied -- the inspector's tag switchboard is
        # a render of exactly this, so it can never offer a toggle for a tag
        # the shot does not emit or miss one it does.
        "cast": resolved["prompt"].get("cast", []),
        "hidden_tags": list((node.data or {}).get("cg_hidden_tags") or []),
        "forced_tags": list((node.data or {}).get("cg_forced_tags") or []),
        "lora": resolved["lora"],
        "lora_weight": resolved["lora_weight"] if resolved["lora"] else 0,
        "face_positive": (resolved["face_prompt"] or {}).get("positive", ""),
        "face_lora": resolved["face_lora"],
        "face_lora_weight": resolved["face_lora_weight"],
    }


class BackgroundRequest(BaseModel):
    location_id: str
    variant: str = "day"
    weather: str = ""
    seed: Optional[int] = None


@router.post("/projects/{project_id}/images/background")
def generate_background(project_id: str, body: BackgroundRequest) -> dict[str, str]:
    return {"job_id": enqueue(project_id, "image.background", body.model_dump(),
                              label=f"bg {body.location_id}/{body.variant}")}


class SpriteRequest(BaseModel):
    character_id: str
    outfit_id: str = ""
    expression: str = "neutral"
    pose: str = "front"
    slot: str = ""
    seed: Optional[int] = None
    extra_positive: str = ""   # free-text prompt tweaks for a one-off regen;
    extra_negative: str = ""   # not persisted onto the character


@router.post("/projects/{project_id}/images/sprite")
def generate_sprite(project_id: str, body: SpriteRequest) -> dict[str, str]:
    character = store.get_character(project_id, body.character_id)
    if character is None:
        raise HTTPException(404, "character not found")
    if character.age < 18:
        raise HTTPException(422, "character is under 18 and cannot reach the image pipeline")
    return {"job_id": enqueue(project_id, "image.sprite", body.model_dump(),
                              label=f"sprite {body.character_id}/{body.expression}")}


class ProfileImageRequest(BaseModel):
    character_id: str
    expression: str = "neutral"
    outfit_id: str = ""
    seed: Optional[int] = None


@router.post("/projects/{project_id}/images/profile")
def generate_profile_image(project_id: str, body: ProfileImageRequest) -> dict[str, str]:
    """A single headshot for the character overview grid -- see
    images.assemble_portrait_prompt. Written onto character.assets.profile_image;
    delete/regenerate is the same save-then-purge the sprite grid uses."""
    character = store.get_character(project_id, body.character_id)
    if character is None:
        raise HTTPException(404, "character not found")
    if character.age < 18:
        raise HTTPException(422, "character is under 18 and cannot reach the image pipeline")
    return {"job_id": enqueue(project_id, "image.profile", body.model_dump(),
                              label=f"profile {body.character_id}")}


class SpriteSetRequest(BaseModel):
    character_id: str
    outfit_ids: list[str] = []
    expressions: list[str] = []
    poses: list[str] = []        # empty = just the default front pose
    seed: Optional[int] = None


@router.post("/projects/{project_id}/images/sprite-set")
def generate_sprite_set(project_id: str, body: SpriteSetRequest) -> dict[str, Any]:
    """Every outfit x pose x expression combination asked for.

    Poses default to `front` alone rather than the whole vocabulary -- the set
    is a product, and eight poses would multiply an ordinary sprite run by
    eight. Batch fill queues the poses the script actually uses instead.
    """
    character = store.get_character(project_id, body.character_id)
    if character is None:
        raise HTTPException(404, "character not found")
    outfits = body.outfit_ids or [o.id for o in character.outfits] or [""]
    expressions = body.expressions or images.DEFAULT_SHEET_EXPRESSIONS
    poses = body.poses or ["front"]
    jobs = []
    for outfit in outfits:
        for pose in poses:
            for expression in expressions:
                jobs.append(enqueue(project_id, "image.sprite", {
                    "character_id": body.character_id, "outfit_id": outfit,
                    "pose": pose, "expression": expression, "seed": body.seed,
                }, label=f"sprite {body.character_id}/{outfit}/{pose}/{expression}"))
    return {"job_ids": jobs, "count": len(jobs)}


class SheetRequest(BaseModel):
    character_id: str
    # How many images to shoot. The selected outfits x angles x expressions
    # are *sampled* to fill this, not multiplied out -- the product reaches
    # three figures on a character with a few outfits, and the LoRA wants
    # 15-30 good images (images.sheet_plan).
    count: int = 30
    outfit_ids: list[str] = []   # empty = every outfit the character has
    angles: list[str] = []
    expressions: list[str] = []
    seed: Optional[int] = None


@router.post("/projects/{project_id}/images/sheet")
def generate_sheet(project_id: str, body: SheetRequest) -> dict[str, str]:
    """Step 2 of the LoRA bootstrap: `count` sampled outfit/angle/expression
    combinations at a fixed seed, across every outfit by default. Appends to
    the character's sheet pool -- never replaces it, so re-running after
    touching up one outfit is safe."""
    return {"job_id": enqueue(project_id, "image.sheet", body.model_dump(),
                              label=f"character sheet {body.character_id}")}


class CgSetRequest(BaseModel):
    character_id: str
    count: int = 24
    outfit_ids: list[str] = []      # empty = every outfit the character has
    angles: list[str] = []
    expressions: list[str] = []
    times: list[str] = []
    location_ids: list[str] = []    # empty = every location in the project
    # What the body is doing, and who else is in frame (defaults.CG_SET_ACTIONS
    # / CG_SET_COMPANY). These are the two axes an event CG actually prompts on
    # -- context and action, from the player's point of view -- and the two the
    # character sheet has no way to teach.
    actions: list[str] = []
    company: list[str] = []
    # Every list here is sampled independently per image; empty means "the
    # whole vocabulary". "Regenerate this one" sets `count: 1` and passes every
    # field as a single-element list pinned to one existing pool item's recipe,
    # so only the seed varies.
    framings: list[str] = []
    scenes: list[str] = []


@router.post("/projects/{project_id}/images/cg-set")
def generate_cg_set(project_id: str, body: CgSetRequest) -> dict[str, str]:
    """Shoot the CG LoRA's own training set, using the Sprite LoRA.

    The Sprite LoRA only ever saw the character sheet, so it knows the face
    and nothing else; this spends that on variety -- real locations, varied
    angles, framings and light -- into `cg_pool`, the CG LoRA's own dataset,
    never mixed with the sheet.
    """
    character = store.get_character(project_id, body.character_id)
    if character is None:
        raise HTTPException(404, "character not found")
    if character.age < 18:
        raise HTTPException(422, "character is under 18 and cannot reach the image pipeline")
    if not character.assets.lora:
        raise HTTPException(
            422, "no Sprite LoRA yet -- the CG training set is shot with it, so train "
            "the Sprite LoRA first")
    return {"job_id": enqueue(project_id, "image.cg_set", body.model_dump(),
                              label=f"CG training set {body.character_id}")}


class CaptionRequest(BaseModel):
    character_id: str
    hashes: list[str]
    # Which pool the hashes came from -- "sheet" (the Sprite LoRA's) or "cg"
    # (the CG LoRA's). Determines which pool the resulting captions are
    # written back onto.
    pool: str = "sheet"


@router.post("/projects/{project_id}/images/caption")
def caption(project_id: str, body: CaptionRequest) -> dict[str, str]:
    """Auto-caption pool images. Captions are written back onto the named
    pool, not just returned."""
    return {"job_id": enqueue(project_id, "image.caption", body.model_dump(),
                              label=f"caption {len(body.hashes)} images")}


class TrainRequest(BaseModel):
    character_id: str
    hashes: list[str] = []       # empty = every image in the slot's own pool
    captions: dict[str, str] = {}
    epochs: int = 10
    network_dim: int = 32
    output_name: str = ""
    # "sprite" (trained on the character sheet, what sprites always load) or
    # "cg" (trained on cg_pool, the varied dataset). Separate files, separate
    # character fields, separate dataset directories on disk: training one
    # never touches the other.
    slot: str = "sprite"


@router.post("/projects/{project_id}/lora/train")
def train_lora(project_id: str, body: TrainRequest) -> dict[str, str]:
    """Step 5: kick off training; the result is written into the character."""
    return {"job_id": enqueue(project_id, "lora.train", body.model_dump(),
                              label=f"LoRA {body.character_id}")}


# --------------------------------------------------------------------------
# TTS
# --------------------------------------------------------------------------

class VoiceRequest(BaseModel):
    node_id: str
    seed: int = 0
    # > 1 on a re-take: that many fresh seeds, all kept as takes to pick from.
    takes: int = 1


@router.post("/projects/{project_id}/tts/line")
def voice_line(project_id: str, body: VoiceRequest) -> dict[str, str]:
    return {"job_id": enqueue(project_id, "tts.line", body.model_dump(),
                              label="voice line", node_id=body.node_id)}


@router.post("/projects/{project_id}/tts/all")
def voice_all(project_id: str, only_missing: bool = True) -> dict[str, Any]:
    """Design for overnight batch runs, not interactive generation (doc S9)."""
    characters = {c.id: c for c in store.list_characters(project_id)}
    voice_refs = {cid: staleness.voice_id(c) for cid, c in characters.items()}
    jobs = []
    for node in store.list_nodes(project_id, include_orphaned=False):
        if node.type != NodeType.say:
            continue
        d = node.data or {}
        speaker = characters.get(d.get("speaker") or "")
        if speaker is None or speaker.is_player or not speaker.voice.voiced:
            continue
        # Evaluated here rather than trusted from the row: a recast changes no
        # node, so the stored status is one sweep behind it.
        staleness.evaluate(node, voice_refs)
        if only_missing and d.get("voice_path") and node.audio_status.value == "ready":
            continue
        jobs.append(enqueue(project_id, "tts.line", {"node_id": node.id, "seed": 0},
                            label=f"voice {speaker.display_name}", node_id=node.id))
    return {"job_ids": jobs, "count": len(jobs)}


class CastTake(BaseModel):
    emotion: str = "neutral"
    intensity: float = 0.6
    text: str = ""


class CastRequest(BaseModel):
    character_id: str
    takes: int = 0                      # 0 = the whole default emotion script
    script: list[CastTake] = []         # explicit emotion/line pairs, wins over both
    lines: list[str] = []               # text-only override of the default takes
    seed_clip: str = ""


@router.post("/projects/{project_id}/tts/cast")
def cast(project_id: str, body: CastRequest) -> dict[str, str]:
    return {"job_id": enqueue(project_id, "tts.cast", body.model_dump(),
                              label=f"casting {body.character_id}")}


@router.get("/projects/{project_id}/tts/casting-lines")
def casting_lines() -> dict[str, Any]:
    return {"takes": tts.CASTING_TAKES, "lines": tts.CASTING_LINES}


# --------------------------------------------------------------------------
# batch
# --------------------------------------------------------------------------

@router.post("/projects/{project_id}/batch/regenerate-stale")
def regenerate_stale(project_id: str, body: dict[str, Any] | None = None) -> dict[str, str]:
    return {"job_id": enqueue(project_id, "batch.regenerate_stale", body or {},
                              label="regenerate stale assets")}


class FillMissing(BaseModel):
    # `cg_prompts` runs the CG director over flagged nodes that have nothing
    # to shoot from, and is listed before `cgs` because that is the order
    # batch.fill_missing queues them in.
    kinds: list[str] = ["backgrounds", "sprites", "cg_prompts", "cgs", "voice"]
    include_stale: bool = True
    limit: int = 0          # 0 = everything


@router.get("/projects/{project_id}/batch/missing")
def missing_report(project_id: str, include_stale: bool = True) -> dict[str, Any]:
    """What an overnight fill would generate, without queueing it."""
    from ..services import batch as batch_svc
    return batch_svc.scan(project_id, include_stale)


@router.post("/projects/{project_id}/batch/fill-missing")
def fill_missing(project_id: str, body: FillMissing) -> dict[str, str]:
    """Queue every missing asset. Built to be started and left overnight."""
    return {"job_id": enqueue(project_id, "batch.fill_missing", body.model_dump(),
                              label="fill missing assets")}
