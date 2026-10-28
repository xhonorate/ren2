"""Find every asset the project is missing, so it can be filled in one run.

Also reports what cannot be generated at all -- sound cues with no file behind
them -- because that list has the same shape and the same purpose: everything
still standing between the script and a finished build, in one scan.

Scoped to what the script actually references: sprites are only queued for
outfit x expression pairs that some node really uses, and backgrounds only for
time-of-day variants some Scene or Stage node actually asks for. Generating the
full cross product would be hours of GPU time on frames nothing shows.
"""
from __future__ import annotations

from typing import Any

from .. import store
from ..graph import Graph, resolve_state
from ..models import AssetStatus, NodeType
from . import images, staleness


def needs_direction(node_data: dict[str, Any]) -> bool:
    """True when a flagged CG node has nothing for the camera to shoot from.

    Mirrors the fallback chain in `jobs.handlers.cg_inputs`, so this answers
    the same question the renderer would: is there any authored intent here,
    or would the shot be invented from the identity block alone? An
    orientation pose does not count -- `images.POSE_CG_TAGS` maps front/side/
    back to the empty string precisely because framing is the director's call
    -- so a node whose only pose is the "front" default is as undirected as
    one with no pose at all.
    """
    d = node_data or {}
    if (d.get("cg_camera") or "").strip() or (d.get("cg_action") or "").strip():
        return False
    if (d.get("shot_hint") or "").strip():
        return False
    return not images.POSE_CG_TAGS.get(d.get("pose", "") or "", "")


def scan(project_id: str, include_stale: bool = True) -> dict[str, Any]:
    """What is missing, itemised and countable, without queueing anything."""
    nodes = store.list_nodes(project_id, include_orphaned=False)
    edges = store.list_edges(project_id)
    graph = Graph.build(nodes, edges)
    characters = {c.id: c for c in store.list_characters(project_id)}
    voice_refs = {cid: staleness.voice_id(c) for cid, c in characters.items()}
    locations = {l.id: l for l in store.list_locations(project_id)}

    voice: list[dict[str, Any]] = []
    sounds: list[dict[str, Any]] = []
    cgs: list[dict[str, Any]] = []
    cg_prompts: list[dict[str, Any]] = []
    backgrounds: list[dict[str, Any]] = []
    sprites: list[dict[str, Any]] = []
    blocked: list[str] = []

    # -- voice lines -------------------------------------------------------
    for node in nodes:
        if node.type != NodeType.say:
            continue
        d = node.data or {}
        speaker = characters.get(d.get("speaker") or "")
        if speaker is None or speaker.is_player or not speaker.voice.voiced:
            continue
        if not (d.get("text") or "").strip():
            continue
        staleness.evaluate(node, voice_refs)
        missing = not d.get("voice_path")
        stale = node.audio_status == AssetStatus.stale
        if missing or (include_stale and stale):
            if not speaker.voice.reference_clip:
                blocked.append(
                    f"{speaker.display_name} has no reference clip — cast the voice first")
                continue
            voice.append({"node_id": node.id, "speaker": speaker.id,
                          "reason": "stale" if stale and not missing else "missing",
                          "text": (d.get("text") or "")[:70]})

    # -- event CGs ---------------------------------------------------------
    for node in nodes:
        d = node.data or {}
        if not d.get("cg"):
            continue
        staleness.evaluate(node)
        missing = not d.get("image_hash")
        stale = node.image_status == AssetStatus.stale
        if missing or (include_stale and stale):
            undirected = needs_direction(d)
            item = {"node_id": node.id,
                    "reason": "stale" if stale and not missing else "missing",
                    "needs_direction": undirected,
                    "text": (d.get("text") or "")[:70]}
            cgs.append(item)
            # Scoped to the CGs this run would actually render: directing a
            # node whose image is already current writes a prompt nothing
            # then uses, and the frame that comes out of it is the point.
            if undirected:
                cg_prompts.append(dict(item))

    # -- backgrounds: only variants the script asks for ---------------------
    wanted: dict[str, set[str]] = {}
    for node in nodes:
        d = node.data or {}
        if node.type == NodeType.scene and d.get("background"):
            wanted.setdefault(d["background"], set()).add(d.get("time_of_day") or "day")
        elif node.type == NodeType.stage and d.get("location"):
            state = resolve_state(graph.path_to(node.id), include_last=True)
            wanted.setdefault(d["location"], set()).add(
                d.get("time_of_day") or state.time_of_day or "day")
    for location_id, variants in sorted(wanted.items()):
        location = locations.get(location_id)
        if location is None:
            blocked.append(f"unknown location '{location_id}' referenced by the script")
            continue
        for variant in sorted(variants):
            if variant not in (location.variants or {}):
                backgrounds.append({"location_id": location_id, "variant": variant,
                                    "reason": "missing"})

    # -- sprites: only outfit x pose x expression combos in use ------------
    used: dict[str, set[tuple[str, str, str]]] = {}
    for node in nodes:
        d = node.data or {}
        if node.type == NodeType.say and d.get("speaker"):
            state = resolve_state(graph.path_to(node.id), include_last=True)
            character = characters.get(d["speaker"])
            if character is None or character.is_player:
                continue
            outfit = state.outfits.get(character.id) or character.default_outfit or ""
            used.setdefault(character.id, set()).add(
                (outfit, d.get("pose") or "front", d.get("expression") or "neutral"))
        elif node.type == NodeType.stage:
            state = resolve_state(graph.path_to(node.id), include_last=True)
            staged = set((d.get("expressions") or {})) | set((d.get("poses") or {}))
            for cid in staged:
                character = characters.get(cid)
                if character is None or character.is_player:
                    continue
                outfit = state.outfits.get(cid) or character.default_outfit or ""
                used.setdefault(cid, set()).add((
                    outfit,
                    state.poses.get(cid) or "front",
                    state.expressions.get(cid) or "neutral",
                ))

    for cid, pairs in sorted(used.items()):
        character = characters[cid]
        if character.age < 18:
            blocked.append(
                f"{character.display_name} is under 18 and is blocked from the image pipeline")
            continue
        for outfit, pose, expression in sorted(pairs):
            slot = images.sprite_slot(outfit, pose, expression)
            if slot not in (character.assets.sprite_sheet or {}):
                sprites.append({"character_id": cid, "outfit_id": outfit, "pose": pose,
                                "expression": expression, "slot": slot, "reason": "missing"})

    # -- unresolved sound cues ---------------------------------------------
    # The writer marks a *description* of the sound a beat makes; a real
    # file only ever arrives by hand. Nothing generates audio, so these are
    # never queued and never counted in `total` -- the list itself is the
    # deliverable, so sound can be sourced in one pass instead of by grepping
    # the compiled script for the `# TODO sfx:` comments an unresolved cue
    # leaves behind.
    scene_order = {scene.id: i for i, scene in enumerate(graph.scenes())}
    for node in nodes:
        d = node.data or {}
        scene = graph.owning_scene(node.id)
        where = {
            "scene_id": scene.id if scene else None,
            "scene": (scene.data or {}).get("title", "") if scene else "",
            "order": scene_order.get(scene.id if scene else "", len(scene_order)),
        }
        cue = (d.get("sfx_cue") or "").strip()
        if cue and not (d.get("sfx_file") or "").strip():
            sounds.append({**where, "node_id": node.id, "kind": "sfx", "cue": cue,
                           "field": "sfx_file",
                           "text": (d.get("text") or "")[:70]})
        music = (d.get("music_cue") or "").strip()
        if music and not (d.get("music_file") or "").strip() and not d.get("stop_music"):
            sounds.append({**where, "node_id": node.id, "kind": "music", "cue": music,
                           "field": "music_file",
                           "text": (d.get("location") or "")})
    sounds.sort(key=lambda item: item["order"])

    return {
        "voice": voice,
        "sounds": sounds,
        "cgs": cgs,
        "cg_prompts": cg_prompts,
        "backgrounds": backgrounds,
        "sprites": sprites,
        "blocked": sorted(set(blocked)),
        "counts": {
            "voice": len(voice), "cgs": len(cgs), "backgrounds": len(backgrounds),
            "sprites": len(sprites),
            # `sounds` deliberately stays out of `total`: total is what a fill
            # run would queue, and no job generates audio. `cg_prompts` stays
            # out for the other reason -- it is a subset of `cgs`, not an
            # asset of its own, and counting it would make the "N missing"
            # headline read higher than the number of files a run produces.
            "sounds": len(sounds),
            "cg_prompts": len(cg_prompts),
            "total": len(voice) + len(cgs) + len(backgrounds) + len(sprites),
        },
        "estimate_seconds": _estimate(len(voice), len(cgs) + len(backgrounds) + len(sprites),
                                      len(cg_prompts)),
    }


def _estimate(voice_lines: int, images: int, cg_prompts: int = 0) -> int:
    """Roughly 5s per voice line and 15s per image on a single 4090 (doc S9).

    A director pass is one short LM Studio call against the structuring model,
    so it costs about half a frame.
    """
    return voice_lines * 5 + images * 15 + cg_prompts * 8
