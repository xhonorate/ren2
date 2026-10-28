"""Route and pacing metrics: how long each route is, and who is in it.

Balancing a branching story is otherwise blind. Route B reads as "about the
same" as route A right up until it is voiced and turns out to be half the
length -- by which point the fix costs an overnight batch. The numbers that
answer it are all already in the graph; nothing here generates or stores
anything, it only counts.

Counted over the whole scene, not one linearized path. `script.linearize` walks
a single route through a branch because that is what an editing surface needs;
a scene's *size* is every word in it, including the branch this playthrough did
not take, because that is the work that had to be written and voiced.

A route needs no registry: it exists because scenes claim it (`SceneData.route`),
the same rule the compiler and hub dispatch already use. Scenes claiming no
route are collected under one unnamed bucket rather than dropped, since a story
usually has a common trunk before the routes split.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .. import store
from ..graph import Graph, resolve_state
from ..models import Character, Location, Node, NodeType

# Words per minute for the playtime estimate. VN dialogue read aloud sits
# around 150; silent click-through is faster but stops at every choice, and the
# two roughly cancel. It is a scale factor on the word count and nothing more --
# useful for "route B is half of route A", not for a store page.
WORDS_PER_MINUTE = 160

_WORD_RE = re.compile(r"[\w'’-]+")

# Node types that put words on screen. Everything else is machinery.
PROSE_TYPES = (NodeType.say, NodeType.narration)


def word_count(text: Any) -> int:
    return len(_WORD_RE.findall(str(text or "")))


def _voiceable(node: Node, characters: dict[str, Character]) -> bool:
    """Whether a TTS job would ever be queued for this line.

    Matches the rule the tts.line handler enforces, so the percentage cannot
    disagree with what the batch actually does.
    """
    if node.type != NodeType.say:
        return False
    speaker = characters.get((node.data or {}).get("speaker") or "")
    return speaker is not None and not speaker.is_player and speaker.voice.voiced


def _branch_depth(graph: Graph, scene: Node, members: set[str]) -> int:
    """Deepest stack of branching nodes on any one path through the scene.

    Not the node count and not the longest path: what makes a scene hard to
    hold in your head is how many decisions are live at once. Edges leaving the
    scene stop the walk -- they are the next scene's depth, not this one's.

    Memoized post-order rather than an all-paths walk, because a scene with a
    few merges has exponentially many paths and only a few hundred nodes. A
    back edge lands on a node still on the stack and contributes nothing, which
    is what keeps a loop-back from counting forever.
    """
    BRANCHING = (NodeType.choice, NodeType.hub, NodeType.condition)
    depth: dict[str, int] = {}
    state: dict[str, int] = {}          # 1 = on the stack, 2 = settled
    stack: list[tuple[str, bool]] = [(scene.id, False)]

    while stack:
        node_id, settling = stack.pop()
        if node_id not in members:
            continue
        if settling:
            node = graph.nodes.get(node_id)
            own = 1 if node is not None and node.type in BRANCHING else 0
            below = max((depth[e.target] for e in graph.out_edges(node_id)
                         if state.get(e.target) == 2), default=0)
            depth[node_id] = own + below
            state[node_id] = 2
            continue
        if state.get(node_id):
            continue
        state[node_id] = 1
        stack.append((node_id, True))
        for edge in graph.out_edges(node_id):
            if edge.target in members and not state.get(edge.target):
                stack.append((edge.target, False))
    return depth.get(scene.id, 0)


def _scene_members(graph: Graph, scenes: list[Node]) -> dict[str, set[str]]:
    """scene id -> the nodes that compile into it, by the same rule as /graph."""
    members: dict[str, set[str]] = {s.id: {s.id} for s in scenes}
    for node in graph.nodes.values():
        owner = graph.owning_scene(node.id)
        if owner is not None and owner.id in members:
            members[owner.id].add(node.id)
    return members


def _blank_totals() -> dict[str, Any]:
    return {"scenes": 0, "words": 0, "lines": 0, "say_lines": 0,
            "narration_lines": 0, "choices": 0, "options": 0, "nodes": 0,
            "voiced": 0, "voiceable": 0, "cgs_done": 0, "cgs": 0,
            "backgrounds_done": 0, "backgrounds": 0, "max_depth": 0}


def _accumulate(into: dict[str, Any], scene: dict[str, Any]) -> None:
    for key, value in scene.items():
        if key == "max_depth" or key == "depth":
            into["max_depth"] = max(into["max_depth"], value)
        elif key in into and isinstance(value, int):
            into[key] += value
    into["scenes"] += 1


def _derive(bucket: dict[str, Any]) -> dict[str, Any]:
    """The ratios, computed once from the counts rather than in the UI."""
    words = bucket["words"]
    lines = bucket["lines"]
    bucket["seconds"] = round(words / WORDS_PER_MINUTE * 60)
    bucket["voiced_pct"] = (round(100 * bucket["voiced"] / bucket["voiceable"])
                            if bucket["voiceable"] else None)
    imageable = bucket["cgs"] + bucket["backgrounds"]
    done = bucket["cgs_done"] + bucket["backgrounds_done"]
    bucket["imaged_pct"] = round(100 * done / imageable) if imageable else None
    bucket["words_per_line"] = round(words / lines, 1) if lines else 0.0
    # Expressed as "a choice every N lines" rather than a ratio: 0.04 says
    # nothing at a glance, "a choice every 25 lines" is the pacing itself.
    bucket["lines_per_choice"] = (round(lines / bucket["choices"], 1)
                                  if bucket["choices"] else None)
    return bucket


def report(project_id: str) -> dict[str, Any]:
    """Every scene measured, grouped by the route it claims."""
    nodes = store.list_nodes(project_id, include_orphaned=False)
    edges = store.list_edges(project_id)
    graph = Graph.build(nodes, edges)
    characters = {c.id: c for c in store.list_characters(project_id)}
    locations = {l.id: l for l in store.list_locations(project_id)}

    scenes = graph.scenes()
    members = _scene_members(graph, scenes)

    # character id -> counts, accumulated across every scene as we go.
    by_character: dict[str, dict[str, Any]] = {
        cid: {"id": cid, "name": c.display_name, "color": c.color,
              "is_player": c.is_player, "lines": 0, "words": 0,
              "voiced": 0, "voiceable": 0, "scenes": 0, "routes": set()}
        for cid, c in characters.items()
    }

    scene_reports: list[dict[str, Any]] = []
    for scene in scenes:
        scene_reports.append(
            _measure_scene(graph, scene, members[scene.id], characters, locations,
                           by_character)
        )

    routes: dict[str, dict[str, Any]] = {}
    for entry in scene_reports:
        bucket = routes.setdefault(entry["route"], {
            "name": entry["route"], "scenes_list": [], **_blank_totals()})
        bucket["scenes_list"].append(entry)
        _accumulate(bucket, entry)

    totals = _blank_totals()
    for entry in scene_reports:
        _accumulate(totals, entry)

    ordered = sorted(routes.values(), key=lambda r: (r["name"] == "", r["name"].lower()))
    for bucket in ordered:
        _derive(bucket)
        bucket["scenes_list"] = [_derive(s) for s in bucket["scenes_list"]]
        bucket["cast"] = _route_cast(bucket["scenes_list"])

    people = []
    for entry in by_character.values():
        entry["routes"] = sorted(entry.pop("routes"))
        if entry["lines"] or not entry["is_player"]:
            entry["voiced_pct"] = (round(100 * entry["voiced"] / entry["voiceable"])
                                   if entry["voiceable"] else None)
            entry["share_pct"] = (round(100 * entry["words"] / totals["words"])
                                  if totals["words"] else 0)
            people.append(entry)
    people.sort(key=lambda p: (-p["words"], p["name"].lower()))

    return {
        "routes": ordered,
        "totals": _derive(totals),
        "characters": people,
        "words_per_minute": WORDS_PER_MINUTE,
    }


def _route_cast(scene_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Who speaks in this route, biggest part first.

    Rolled up from the scenes rather than counted again, so a route's cast and
    its scene rows can never disagree.
    """
    merged: dict[str, dict[str, Any]] = {}
    for entry in scene_entries:
        for speaker in entry["speakers"]:
            row = merged.setdefault(speaker["id"], {
                "id": speaker["id"], "name": speaker["name"],
                "color": speaker["color"], "lines": 0, "words": 0})
            row["lines"] += speaker["lines"]
            row["words"] += speaker["words"]
    total = sum(row["words"] for row in merged.values())
    for row in merged.values():
        row["share_pct"] = round(100 * row["words"] / total) if total else 0
    return sorted(merged.values(), key=lambda r: (-r["words"], r["name"].lower()))


def _measure_scene(graph: Graph, scene: Node, member_ids: set[str],
                   characters: dict[str, Character],
                   locations: dict[str, Location],
                   by_character: dict[str, dict[str, Any]]) -> dict[str, Any]:
    d = scene.data or {}
    entry: dict[str, Any] = {
        "id": scene.id,
        "title": d.get("title") or "Untitled",
        "route": (d.get("route") or "").strip(),
        "nsfw": bool(d.get("nsfw")),
        "words": 0, "lines": 0, "say_lines": 0, "narration_lines": 0,
        "choices": 0, "options": 0, "nodes": len(member_ids),
        "voiced": 0, "voiceable": 0,
        "cgs": 0, "cgs_done": 0, "backgrounds": 0, "backgrounds_done": 0,
    }
    speakers: dict[str, dict[str, Any]] = {}

    for node_id in member_ids:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        nd = node.data or {}

        if node.type in PROSE_TYPES:
            words = word_count(nd.get("text"))
            entry["words"] += words
            entry["lines"] += 1
            if node.type == NodeType.say:
                entry["say_lines"] += 1
                cid = nd.get("speaker") or ""
                person = characters.get(cid)
                if person is not None:
                    row = speakers.setdefault(cid, {
                        "id": cid, "name": person.display_name,
                        "color": person.color, "lines": 0, "words": 0})
                    row["lines"] += 1
                    row["words"] += words
                    tally = by_character.get(cid)
                    if tally is not None:
                        tally["lines"] += 1
                        tally["words"] += words
                        if entry["route"]:
                            tally["routes"].add(entry["route"])
                if _voiceable(node, characters):
                    entry["voiceable"] += 1
                    if nd.get("voice_path"):
                        entry["voiced"] += 1
                    tally = by_character.get(cid)
                    if tally is not None:
                        tally["voiceable"] += 1
                        if nd.get("voice_path"):
                            tally["voiced"] += 1
            else:
                entry["narration_lines"] += 1

        if node.type in (NodeType.choice, NodeType.hub):
            entry["choices"] += 1
            entry["options"] += len(nd.get("options") or [])

        # Event CGs are the only per-node image; sprites belong to a character
        # and are counted on the Characters tab, not against a scene's length.
        if nd.get("cg"):
            entry["cgs"] += 1
            if nd.get("image_hash"):
                entry["cgs_done"] += 1

    entry["backgrounds"], entry["backgrounds_done"] = _background_coverage(
        graph, scene, member_ids, locations)
    entry["depth"] = _branch_depth(graph, scene, member_ids)
    entry["speakers"] = sorted(speakers.values(),
                               key=lambda s: (-s["words"], s["name"].lower()))
    for cid in speakers:
        tally = by_character.get(cid)
        if tally is not None:
            tally["scenes"] += 1
    return entry


def _background_coverage(graph: Graph, scene: Node, member_ids: set[str],
                         locations: dict[str, Location]) -> tuple[int, int]:
    """(wanted, rendered) location x time-of-day variants this scene shows.

    Same scoping rule as the batch scan: only variants the script actually
    asks for, never the full cross product.
    """
    wanted: set[tuple[str, str]] = set()
    for node_id in member_ids:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        nd = node.data or {}
        if node.type == NodeType.scene and nd.get("background"):
            wanted.add((nd["background"], nd.get("time_of_day") or "day"))
        elif node.type == NodeType.stage and nd.get("location"):
            state = resolve_state(graph.path_to(node.id), include_last=True)
            wanted.add((nd["location"],
                        nd.get("time_of_day") or state.time_of_day or "day"))

    done = 0
    for location_id, variant in wanted:
        location = locations.get(location_id)
        if location is not None and variant in (location.variants or {}):
            done += 1
    return len(wanted), done


def rename_route(project_id: str, old: str, new: str) -> int:
    """Rename a route across every scene claiming it, and every hub targeting it.

    Both halves or neither: a route lives in two places -- the scenes that claim
    it and the `target_route` on hub options that dispatch over it -- and
    renaming only the scenes turns every one of those options into a validation
    error pointing at a route nobody belongs to.
    """
    old, new = (old or "").strip(), (new or "").strip()
    if not old or old == new:
        return 0
    touched = 0
    for node in store.list_nodes(project_id):
        d = node.data or {}
        if node.type == NodeType.scene and (d.get("route") or "").strip() == old:
            node.data = {**d, "route": new}
            store.update_node(project_id, node)
            touched += 1
        elif node.type == NodeType.hub:
            options = d.get("options") or []
            if not any((o.get("target_route") or "").strip() == old for o in options):
                continue
            node.data = {**d, "options": [
                {**o, "target_route": new}
                if (o.get("target_route") or "").strip() == old else o
                for o in options
            ]}
            store.update_node(project_id, node)
            touched += 1
    return touched
