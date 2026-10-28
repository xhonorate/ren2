"""IR -> web preview player program.

The second compile target (doc S0). A few hundred lines of interpreter in the
browser lets you test a branch in one second instead of launching Ren'Py.
It runs off the same label set as the Ren'Py compiler, so what you play is
what you build.
"""
from __future__ import annotations

from typing import Any, Optional

from ..graph import Graph, resolve_state, slugify, stage_clears, stat_condition_var
from ..models import Character, Edge, Location, Node, NodeType, ProjectMeta
from ..services import images
from .labels import compute_labels


def _nearest_sprite(char: Character, outfit: str, pose: str, expression: str) -> Optional[str]:
    """The closest generated sprite to what the state asks for.

    Same fallback order as the Ren'Py compiler (outfit held hardest, pose
    given up first) so the preview shows what the built game would.
    """
    sheet = char.assets.sprite_sheet or {}
    for candidate in (
        images.sprite_slot(outfit, pose, expression),
        images.sprite_slot(outfit, "front", expression),
        images.sprite_slot(outfit, pose, "neutral"),
        images.sprite_slot(outfit, "front", "neutral"),
        # Pre-pose keys, so a character that has not been regenerated since
        # poses landed still shows something.
        f"{outfit}/{expression}",
        f"{outfit}/neutral",
    ):
        if sheet.get(candidate):
            return sheet[candidate]
    for slot, asset_hash in sorted(sheet.items()):
        if asset_hash and images.parse_sprite_slot(slot)[0] == outfit:
            return asset_hash
    return None


class PreviewCompiler:
    def __init__(self, project: ProjectMeta, nodes: list[Node], edges: list[Edge],
                 characters: list[Character], locations: list[Location]) -> None:
        self.project = project
        self.graph = Graph.build(nodes, edges)
        self.characters = {c.id: c for c in characters}
        self.locations = {l.id: l for l in locations}
        self.warnings: list[str] = []
        self.labels: dict[str, str] = {}

    ENTRY_FROM_NODE = "__from_node__"

    def _is_player(self, cid: Optional[str]) -> bool:
        c = self.characters.get(cid or "")
        return bool(c and c.is_player)

    def compile(self, start_node_id: Optional[str] = None) -> dict[str, Any]:
        self.labels = compute_labels(self.graph, self.warnings)
        blocks: dict[str, list[dict[str, Any]]] = {}
        for node_id, name in self.labels.items():
            blocks[name] = self._run(node_id, first=True)

        entry = None
        scenes = self.graph.scenes()
        if scenes:
            entry = self.labels.get(scenes[0].id)
        if entry is None and self.labels:
            entry = sorted(self.labels.values())[0]

        if start_node_id and start_node_id in self.graph.nodes:
            # Play from an arbitrary node: reuse its label if it has one, else
            # synthesise a block so any node in the graph is directly playable.
            blocks[self.ENTRY_FROM_NODE] = (
                self._restore_stage(start_node_id) + self._run(start_node_id, first=True)
            )
            entry = self.ENTRY_FROM_NODE

        return {
            "entry": entry,
            "blocks": blocks,
            "labels": {v: k for k, v in self.labels.items()},   # label -> node id
            "characters": {
                cid: {
                    "id": cid,
                    "name": c.display_name,
                    "color": c.color,
                    "is_player": c.is_player,
                    "voiced": c.voice.voiced,
                }
                for cid, c in self.characters.items()
            },
            "locations": {
                lid: {"id": lid, "name": l.name, "variants": l.variants}
                for lid, l in self.locations.items()
            },
            "flags": self._flag_defaults(),
            # route name -> its scene labels in canvas order. The player marks
            # each one visited as it enters, which is how a hub option that
            # targets a route knows which scene is next.
            "routes": self._routes(),
            "warnings": self.warnings,
            "scenes": [
                {"id": s.id, "label": self.labels.get(s.id),
                 "title": (s.data or {}).get("title", "Untitled scene")}
                for s in scenes
            ],
        }

    def _restore_stage(self, node_id: str) -> list[dict[str, Any]]:
        """Rebuild the visible stage for a mid-scene start.

        The background and everyone on screen were established by nodes
        upstream, so starting cold would play the scene against a blank
        backdrop. Path state already knows all of it -- replay it as setup.

        Every op here is `restored`, so the player can tell setup from the
        node's own ops, and carries the upstream `source` node that actually
        set it -- the node the player opens when the backdrop or a sprite is
        clicked, which is never the node playback started from.
        """
        path = self.graph.path_to(node_id)
        upstream = path[:-1]
        state = resolve_state(path, include_last=False)
        ops: list[dict[str, Any]] = [{
            "op": "scene",
            "node": node_id,
            "background": state.location,
            "time_of_day": state.time_of_day,
            "weather": state.weather,
            "transition": "none",
            "restored": True,
            "source": _last_touching(upstream, _sets_backdrop),
        }]
        for cid in state.present:
            if self._is_player(cid):
                continue
            ops.append({
                **self._show(cid, state, node_id),
                "restored": True,
                "source": _last_touching(upstream, lambda n, cid=cid: _stages(n, cid)),
            })
        if state.cg_image:
            ops.append({"op": "cg", "node": node_id, "image": state.cg_image,
                        "transition": "none", "restored": True, "source": state.cg_node})
        return ops

    def _flag_defaults(self) -> dict[str, Any]:
        from ..graph import merged_flag_defaults
        return merged_flag_defaults(self.project, self.graph.nodes.values(),
                                    self.characters.values())

    # ------------------------------------------------------------------

    def _run(self, node_id: str, first: bool = False) -> list[dict[str, Any]]:
        ops: list[dict[str, Any]] = []
        current: Optional[str] = node_id
        started = False
        guard = 0

        while current and guard < 5000:
            guard += 1
            if started and current in self.labels:
                ops.append({"op": "jump", "target": self.labels[current]})
                return ops
            node = self.graph.nodes.get(current)
            if node is None:
                return ops
            started = True

            ops.extend(self._ops_for(node))

            if node.type == NodeType.choice:
                ops.append(self._menu(node))
                return ops
            if node.type == NodeType.hub:
                ops.append(self._hub(node))
                return ops
            if node.type == NodeType.condition:
                ops.append(self._branch(node))
                return ops
            if node.type == NodeType.end:
                return ops
            if node.type == NodeType.jump:
                # `route` is resolved by the player, not here: which scene is
                # next depends on what this run has already played. The wired
                # target rides along as the fallback for an exhausted route,
                # the same way the compiled `else` branch does.
                route = ((node.data or {}).get("target_route") or "").strip()
                target = self.graph.successor(node.id, "out")
                label = self.labels.get(target or "") or None
                if route:
                    ops.append({"op": "jump", "route": route, "target": label})
                else:
                    ops.append({"op": "jump", "target": label} if target
                               else {"op": "return"})
                return ops
            if node.type == NodeType.call:
                route = ((node.data or {}).get("target_route") or "").strip()
                target = self.graph.successor(node.id, "out")
                label = self.labels.get(target or "") or None
                if route:
                    ops.append({"op": "call", "route": route, "target": label})
                elif label:
                    ops.append({"op": "call", "target": label})
                nxt = self.graph.successor(node.id, "after")
                if not nxt:
                    ops.append({"op": "return"})
                    return ops
                current = nxt
                continue

            nxt = self.graph.default_successor(node.id)
            if not nxt:
                ops.append({"op": "return"})
                return ops
            current = nxt
        return ops

    def _ops_for(self, node: Node) -> list[dict[str, Any]]:
        d = node.data or {}
        t = node.type

        if t == NodeType.scene:
            return [{
                "op": "scene",
                "node": node.id,
                "background": d.get("background"),
                "time_of_day": d.get("time_of_day"),
                "weather": d.get("weather"),
                "transition": d.get("transition", "dissolve"),
                "music": d.get("music"),
                "stop_music": bool(d.get("stop_music")),
                "title": d.get("title", ""),
            }]

        if t == NodeType.stage:
            ops: list[dict[str, Any]] = []
            path = self.graph.path_to(node.id)
            state = resolve_state(path, include_last=True)
            if d.get("reset") or d.get("location"):
                ops.append({
                    "op": "scene", "node": node.id,
                    "background": d.get("location") or state.location,
                    "time_of_day": d.get("time_of_day") or state.time_of_day,
                    "transition": d.get("transition", "none"),
                    "keep_sprites": not d.get("reset"),
                    # The build only emits `scene` -- which is what takes a
                    # held CG down -- when the stage really moves.
                    "keep_cg": not stage_clears(
                        d, resolve_state(path, include_last=False).location),
                })
            for cid in d.get("hide", []) or []:
                if self._is_player(cid):
                    continue
                ops.append({"op": "hide", "node": node.id, "character": cid})
            shown = list(dict.fromkeys((d.get("show") or [])
                                       + list((d.get("expressions") or {}).keys())
                                       + list((d.get("outfits") or {}).keys())))
            for cid in shown:
                if cid in (d.get("hide") or []):
                    continue
                # The player is never rendered as a sprite -- skip silently.
                if self._is_player(cid):
                    continue
                ops.append(self._show(cid, state, node.id))
            return ops

        if t == NodeType.say:
            speaker = d.get("speaker")
            char = self.characters.get(speaker or "")
            ops = []
            cg_before, cg_after = self._cg_ops(node)
            if char and not char.is_player:
                prev = resolve_state(self.graph.path_to(node.id), include_last=False)
                expr = d.get("expression") or "neutral"
                pose = d.get("pose") or "front"
                # Same test as the Ren'Py compiler: a pose change alone is a
                # different sprite, and was invisible here while the build
                # showed it.
                if (prev.expressions.get(speaker) != expr
                        or prev.poses.get(speaker, "front") != pose
                        or speaker not in prev.present):
                    after = resolve_state(self.graph.path_to(node.id), include_last=True)
                    ops.append(self._show(speaker, after, node.id))
            ops.extend(cg_before)
            ops.append({
                "op": "say",
                "node": node.id,
                "speaker": speaker,
                "name": char.display_name if char else None,
                "color": char.color if char else None,
                "text": d.get("text", ""),
                # The asset hash, which the browser can fetch -- `voice_path` is
                # where the file lands inside a Ren'Py build, and was being sent
                # here instead, so the player had nothing it could play. Gated
                # exactly as the Ren'Py compiler gates its `voice` statement, so
                # a line is voiced here only if it is voiced in the build.
                "voice": (d.get("voice_hash")
                          if (d.get("voice_path") and char and not char.is_player
                              and char.voice.voiced) else None),
                "cg": bool(d.get("cg")),
                "image": d.get("image_hash"),
                "emotion": d.get("emotion"),
            })
            return ops + cg_after

        if t == NodeType.narration:
            cg_before, cg_after = self._cg_ops(node)
            return cg_before + [{"op": "say", "node": node.id, "speaker": None, "name": None,
                                 "text": d.get("text", ""), "cg": bool(d.get("cg")),
                                 "image": d.get("image_hash")}] + cg_after

        if t == NodeType.set:
            return _preview_effects(d.get("assignments"), node.id)

        if t == NodeType.audio:
            return [{"op": "audio", "node": node.id, "channel": d.get("channel", "music"),
                     "action": d.get("action", "play"), "file": d.get("file"),
                     "fadeout": d.get("fadeout", 1.0)}]

        if t == NodeType.transition:
            return [{"op": "transition", "node": node.id,
                     "transition": d.get("transition", "dissolve"),
                     "pause": d.get("pause", 0.0)}]

        if t == NodeType.raw:
            # The preview player cannot execute Ren'Py; it shows the block so
            # you know something happens here.
            return [{"op": "raw", "node": node.id, "code": d.get("code", "")}]

        if t == NodeType.end:
            ops = [{"op": "unlock", "node": node.id, "name": p}
                   for p in (d.get("unlock_persistent") or [])]
            ops.append({"op": "end", "node": node.id, "mode": d.get("mode", "return"),
                        "ending_id": d.get("ending_id", "")})
            return ops

        return []

    def _cg_ops(self, node: Node) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(before, after) for a line's event CG -- the Ren'Py compiler's
        `_cg_statements`, as ops. `image: None` takes the CG down."""
        d = node.data or {}
        if d.get("cg"):
            before = []
            if d.get("image_hash"):
                held = resolve_state(self.graph.path_to(node.id), include_last=False).cg_node
                before.append({"op": "cg", "node": node.id, "image": d["image_hash"],
                               "transition": "dissolve" if held else "none"})
            after = [] if d.get("cg_hold") else [
                {"op": "cg", "node": node.id, "image": None, "transition": "none"}]
            return before, after
        if d.get("cg_clear"):
            return [{"op": "cg", "node": node.id, "image": None, "transition": "dissolve"}], []
        return [], []

    def _show(self, cid: str, state, node_id: str) -> dict[str, Any]:
        char = self.characters.get(cid)
        outfit = state.outfits.get(cid) or (char.default_outfit if char else "")
        expression = state.expressions.get(cid) or "neutral"
        pose = state.poses.get(cid) or "front"
        return {
            "op": "show",
            "node": node_id,
            "character": cid,
            "name": char.display_name if char else cid,
            "outfit": outfit,
            "expression": expression,
            "pose": pose,
            "position": state.positions.get(cid) or "center",
            "sprite": _nearest_sprite(char, outfit, pose, expression) if char else None,
        }

    def _routes(self) -> dict[str, list[str]]:
        routes: dict[str, list[str]] = {}
        for scene in self.graph.scenes():
            name = ((scene.data or {}).get("route") or "").strip()
            label = self.labels.get(scene.id)
            if name and label:
                routes.setdefault(name, []).append(label)
        return routes

    def _hub(self, node: Node) -> dict[str, Any]:
        """A hub is a menu whose conditions arrive structured.

        The player evaluates `requires` field by field rather than eval'ing an
        expression: the conditions are already `{var, op, value}` triples, and
        a Python expression is not a JavaScript one (`True` is not `true`).
        """
        d = node.data or {}
        options = []
        for opt in d.get("options") or []:
            route = (opt.get("target_route") or "").strip()
            target = self.graph.successor(node.id, opt.get("id", ""))
            requires = _preview_requires(opt)
            options.append({
                "id": opt.get("id"),
                "label": opt.get("label", "..."),
                "requires": requires,
                "locked": opt.get("locked") or "hide",
                "locked_label": opt.get("locked_label") or "",
                "repeatable": bool(opt.get("repeatable")),
                "route": route,
                "effects": [e for e in (opt.get("effects") or []) if e.get("var")],
                "target": self.labels.get(target or ""),
                "dead_end": target is None and not route,
            })
        return {
            "op": "menu",
            "hub": True,
            "node": node.id,
            "prompt": d.get("prompt", ""),
            "mode": d.get("mode", "call"),
            "exhaust": bool(d.get("exhaust", True)),
            "menuset": d.get("menuset") or "hub_topics",
            "loop_back": bool(d.get("loop_back", True)),
            "self_label": self.labels.get(node.id),
            "options": options,
        }

    def _menu(self, node: Node) -> dict[str, Any]:
        d = node.data or {}
        options = []
        for opt in d.get("options") or []:
            target = self.graph.successor(node.id, opt.get("id", ""))
            options.append({
                "id": opt.get("id"),
                "label": opt.get("label", "..."),
                "condition": opt.get("condition", ""),
                "requires": _preview_requires(opt),
                "effects": [e for e in (opt.get("effects") or []) if e.get("var")],
                "target": self.labels.get(target or ""),
                "dead_end": target is None,
            })
        return {
            "op": "menu",
            "node": node.id,
            "prompt": d.get("prompt", ""),
            "mode": d.get("mode", "jump"),
            "exhaust": bool(d.get("exhaust")),
            "menuset": d.get("menuset") or "menuset",
            "loop_back": bool(d.get("loop_back")),
            "self_label": self.labels.get(node.id),
            "options": options,
        }

    def _branch(self, node: Node) -> dict[str, Any]:
        d = node.data or {}
        branches = []
        for branch in d.get("branches") or []:
            target = self.graph.successor(node.id, branch.get("id", ""))
            body = (self._run(target) if target and target not in self.labels
                    else ([{"op": "jump", "target": self.labels[target]}] if target else []))
            branches.append({
                "expr": branch.get("expr", ""),
                "requires": _preview_requires(branch),
                "ops": _preview_effects(branch.get("effects"), node.id) + body,
            })
        if (not any(not b["expr"] and not b["requires"] for b in branches)
                and d.get("has_else", True)):
            fallthrough = self.graph.successor(node.id, "else")
            branches.append({
                "expr": "",
                "requires": [],
                "ops": ([{"op": "jump", "target": self.labels[fallthrough]}]
                        if fallthrough and fallthrough in self.labels
                        else (self._run(fallthrough) if fallthrough else [])),
            })
        return {"op": "if", "node": node.id, "branches": branches}


def _last_touching(path: list[Node], touches) -> Optional[str]:
    """The nearest node on `path`, walking back, that `touches` accepts."""
    for node in reversed(path):
        if touches(node):
            return node.id
    return None


def _sets_backdrop(node: Node) -> bool:
    d = node.data or {}
    if node.type == NodeType.scene:
        return bool(d.get("background") or d.get("time_of_day"))
    if node.type == NodeType.stage:
        return bool(d.get("location") or d.get("time_of_day"))
    return False


def _stages(node: Node, cid: str) -> bool:
    """Whether `node` put `cid` on stage or changed how they look."""
    d = node.data or {}
    if node.type == NodeType.say:
        return d.get("speaker") == cid
    if node.type == NodeType.stage:
        return any(cid in (d.get(key) or ())
                   for key in ("show", "expressions", "outfits", "poses", "positions"))
    return False


def _preview_effects(effects: Any, node_id: str) -> list[dict[str, Any]]:
    """`{var, op, value}` triples as player `set` ops -- one shape whether they
    came off a Set node or a condition branch."""
    return [{"op": "set", "node": node_id, "var": a.get("var"),
             "operator": a.get("op", "="), "value": a.get("value", "")}
            for a in (effects or []) if a.get("var")]


def _preview_requires(holder: dict[str, Any]) -> list[dict[str, Any]]:
    """Structured gates flattened to `{var, op, value}` for the player.

    The player compares field by field rather than eval'ing: a Python
    expression is not a JavaScript one, and these triples are exactly what the
    Ren'Py compiler builds its `if` clause from, so the two agree.
    """
    out = []
    for cond in holder.get("requires") or []:
        var = stat_condition_var(cond)
        if var:
            out.append({"var": var, "op": cond.get("op") or ">=",
                        "value": cond.get("value", "")})
    return out


def compile_preview(project: ProjectMeta, nodes: list[Node], edges: list[Edge],
                    characters: list[Character], locations: list[Location],
                    start_node_id: Optional[str] = None) -> dict[str, Any]:
    return PreviewCompiler(project, nodes, edges,
                           characters, locations).compile(start_node_id)
