"""IR -> Ren'Py compiler.

One way only. Emits into game/generated/; every other file in the Ren'Py
project is hand-owned and never touched (doc S7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .labels import compute_labels
from ..graph import (
    Graph,
    label_name,
    merged_flag_defaults,
    resolve_state,
    slugify,
    stage_clears,
    stat_condition_var,
)
from ..models import (
    Character,
    Edge,
    Location,
    Node,
    NodeType,
    ProjectMeta,
)
from ..services import images

IND = "    "

# Where event CGs sit on the master layer. Sprites are shown at the default 0,
# and a newly shown tag goes on top of everything already there -- so without
# this, a character staged while a held CG is up pops in over the picture.
CG_ZORDER = 10

# Presentation knobs the Build view exposes (Config -> Presentation). The
# compiler bakes them into game/generated/theme.rpy; the write-once
# game/screens.rpy scaffold reads them from there.
PRESENTATION_DEFAULTS: dict[str, Any] = {
    "accent_color": "#e0819b",
    "choice_buttons": True,
    "choice_dim": 0.6,
    "textbox_opacity": 0.85,
    "text_size": 33,
    "text_cps": 0,
    "dialogue_font": "",
    "quick_menu": True,
    "nvl_narration": False,
}


def _pybool(value: Any) -> str:
    return "True" if value else "False"


def _clamp01(value: Any, fallback: float) -> float:
    try:
        return round(min(1.0, max(0.0, float(value))), 3)
    except (TypeError, ValueError):
        return fallback


def q(text: str) -> str:
    """Ren'Py string literal. [] and {} are interpolation/tag syntax."""
    t = (text or "").replace("\\", "\\\\").replace('"', '\\"')
    t = t.replace("[", "[[").replace("{", "{{")
    t = t.replace("\r\n", "\n").replace("\n", " ")
    return '"' + t + '"'


def _menuset_name(node: Node) -> Optional[str]:
    """The `menu (set=...)` name this node needs declaring, if any.

    The defaults have to match the payload models the router validates against
    (ChoiceData.exhaust is off, HubData.exhaust is on), because the compiler
    reads raw node dicts rather than the models.
    """
    d = node.data or {}
    if node.type == NodeType.hub and d.get("exhaust", True):
        return d.get("menuset") or "hub_topics"
    if node.type == NodeType.choice and d.get("exhaust"):
        return d.get("menuset") or "menuset"
    return None


def with_clause(transition: str) -> str:
    t = (transition or "none").strip()
    return "" if t in ("", "none") else f" with {t}"


@dataclass
class CompileResult:
    files: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)   # node id -> label
    warnings: list[str] = field(default_factory=list)
    assets: list[dict[str, str]] = field(default_factory=list)  # copy manifest
    entry: Optional[str] = None                          # label the story starts at
    voice_lines: int = 0
    sound_lines: int = 0                                 # resolved sfx/music cues
    stats: dict[str, int] = field(default_factory=dict)


class RenpyCompiler:
    def __init__(
        self,
        project: ProjectMeta,
        nodes: list[Node],
        edges: list[Edge],
        characters: list[Character],
        locations: list[Location],
        sprite_size: tuple[int, int] = (832, 1216),
        bg_size: tuple[int, int] = (1344, 768),
        presentation: Optional[dict[str, Any]] = None,
    ) -> None:
        self.project = project
        self.graph = Graph.build(nodes, edges)
        self.characters = {c.id: c for c in characters}
        self.locations = {l.id: l for l in locations}
        self.sprite_size = sprite_size
        self.bg_size = bg_size
        self.presentation = {**PRESENTATION_DEFAULTS, **(presentation or {})}
        self.result = CompileResult()
        self.labels: dict[str, str] = {}
        self.routes: dict[str, list[str]] = {}

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    def compile(self) -> CompileResult:
        self.labels = self._labels_with_full_coverage()
        self.result.labels = dict(self.labels)
        # Needs the labels: a route is a list of scene *labels*, and both the
        # flags file and every hub option that targets one read it.
        self.routes = self._routes()

        self.result.files["game/generated/characters.rpy"] = self._emit_characters()
        self.result.files["game/generated/images.rpy"] = self._emit_images()
        self.result.files["game/generated/flags.rpy"] = self._emit_flags()
        self.result.files["game/generated/theme.rpy"] = self._emit_theme()

        for path, body in self._emit_scene_files().items():
            self.result.files[path] = body

        self.result.entry = self._entry_label()

        self.result.stats = {
            "nodes": len(self.graph.nodes),
            "edges": len(self.graph.edges),
            "labels": len(self.labels),
            "scenes": len(self.graph.scenes()),
            "characters": len(self.characters),
        }
        return self.result

    # ------------------------------------------------------------------
    # labels
    # ------------------------------------------------------------------

    def _labels_with_full_coverage(self) -> dict[str, str]:
        return compute_labels(self.graph, self.result.warnings)

    def _entry_label(self) -> Optional[str]:
        """Where the story actually begins -- the first Scene container.

        Never the alphabetically-first label: script.rpy jumps here, and a
        scaffolded entry point that starts mid-story is worse than none.
        """
        for scene in self.graph.scenes():
            if scene.id in self.labels:
                return self.labels[scene.id]
        for root in self.graph.roots():
            if root.id in self.labels:
                return self.labels[root.id]
        return next(iter(sorted(self.labels.values())), None)

    # ------------------------------------------------------------------
    # generated/characters.rpy
    # ------------------------------------------------------------------

    def _emit_characters(self) -> str:
        out = [self._header(), ""]
        for cid in sorted(self.characters):
            c = self.characters[cid]
            var = slugify(c.id, "chr")
            args = [q(c.display_name), f'color="{c.color}"']
            if not c.is_player:
                # image= lets `show aiko smiling` follow the say statement,
                # voice_tag= gives players per-character muting in prefs.
                args.append(f'image="{var}"')
                if c.voice.voiced:
                    args.append(f'voice_tag="{var}"')
            out.append(f"define {var} = Character({', '.join(args)})")
        out.append("")
        if self.presentation.get("nvl_narration"):
            # Narrator lines accumulate on the NVL screen; spoken dialogue
            # stays in the ADV textbox. Scene containers emit `nvl clear`.
            out.append("define narrator = Character(None, kind=nvl)")
        else:
            out.append("# narrator uses the default Character()")
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------
    # generated/images.rpy
    # ------------------------------------------------------------------

    def _scale_transforms(self) -> list[str]:
        """Scale generated art to whatever resolution the project runs at.

        Generated sprites are tall (832x1216 by default) and backgrounds wide,
        but nothing makes the Ren'Py project match: with no gui.init() in
        options.rpy the game runs at Ren'Py's raw 800x600. Shown unscaled, a
        1216px sprite does not simply overflow -- config.default_transform
        anchors images bottom-centre (`xpos 0.5 xanchor 0.5 ypos 1.0 yanchor
        1.0`), so the feet stay on screen and the head is cut off the top.

        Scaling against config.screen_* rather than a baked-in zoom means this
        stays right if the project later sets its own resolution.
        """
        sw, sh = self.sprite_size
        bw, bh = self.bg_size
        return [
            "# ---- scaling " + "-" * 54,
            f"# Art is generated at {sw}x{sh} (sprites) and {bw}x{bh} (backgrounds, CGs).",
            "# Override either transform in custom.rpy; this file is regenerated.",
            f"define vn_sprite_source_height = {sh}",
            f"define vn_bg_source_size = ({bw}, {bh})",
            "# Fraction of screen height a standing sprite occupies.",
            "define vn_sprite_fill = 0.95",
            "",
            "transform vn_sprite:",
            "    zoom (1.0 * config.screen_height * vn_sprite_fill / vn_sprite_source_height)",
            "",
            "transform vn_bg:",
            "    zoom max(1.0 * config.screen_width / vn_bg_source_size[0],"
            " 1.0 * config.screen_height / vn_bg_source_size[1])",
            "",
        ]

    @staticmethod
    def _scaled(path: str, transform: str) -> str:
        return f'At("{path}", {transform})'

    def _emit_images(self) -> str:
        out = [self._header(), ""]
        out.extend(self._scale_transforms())

        out.append("# ---- backgrounds " + "-" * 50)
        for lid in sorted(self.locations):
            loc = self.locations[lid]
            base = slugify(loc.id, "loc")
            if loc.variants:
                for variant in sorted(loc.variants):
                    name = f"bg_{base}_{slugify(variant, 'v')}"
                    out.append(f'image {name} = {self._scaled(f"images/bg/{name}.png", "vn_bg")}')
                    self.result.assets.append(
                        {"hash": loc.variants[variant], "dest": f"game/images/bg/{name}.png"}
                    )
            else:
                out.append(
                    f'image bg_{base} = {self._scaled(f"images/bg/bg_{base}.png", "vn_bg")}')
        out.append("")

        out.append("# ---- sprites " + "-" * 54)
        used = self._used_sprite_combos()
        for cid in sorted(self.characters):
            c = self.characters[cid]
            # The player is never drawn as a sprite: emit no `image`
            # declarations, so nothing downstream can `show` them either.
            if c.is_player:
                continue
            out.extend(self._emit_character_images(c, used.get(cid, set())))
            out.append("")

        out.append("# ---- event CGs " + "-" * 52)
        for node in sorted(self.graph.nodes.values(), key=lambda n: n.id):
            d = node.data or {}
            if not d.get("cg") or not d.get("image_hash"):
                continue
            name = self._cg_name(node)
            out.append(f'image {name} = {self._scaled(f"images/cg/{name}.png", "vn_bg")}')
            self.result.assets.append(
                {"hash": d["image_hash"], "dest": f"game/images/cg/{name}.png"}
            )
        return "\n".join(out) + "\n"

    def _sprite_combos(self, c: Character) -> dict[tuple[str, str, str], str]:
        """(outfit, pose, expression) -> asset hash, from what actually exists."""
        combos: dict[tuple[str, str, str], str] = {}
        for slot, asset_hash in (c.assets.sprite_sheet or {}).items():
            if asset_hash:
                combos[images.parse_sprite_slot(slot)] = asset_hash
        return combos

    def _used_sprite_combos(self) -> dict[str, set[tuple[str, str, str]]]:
        """(outfit, pose, expression) each character is actually shown in."""
        used: dict[str, set[tuple[str, str, str]]] = {}

        def record(cid: str, state) -> None:
            c = self.characters.get(cid)
            if c is None or c.is_player:
                return
            used.setdefault(cid, set()).add((
                state.outfits.get(cid) or c.default_outfit or "",
                state.poses.get(cid) or "front",
                state.expressions.get(cid) or "neutral",
            ))

        for node in self.graph.nodes.values():
            d = node.data or {}
            if node.type == NodeType.say and d.get("speaker"):
                record(d["speaker"], resolve_state(self.graph.path_to(node.id), include_last=True))
            elif node.type == NodeType.stage:
                state = resolve_state(self.graph.path_to(node.id), include_last=True)
                for cid in (set(d.get("show") or []) | set(d.get("expressions") or {})
                            | set(d.get("poses") or {}) | set(d.get("outfits") or {})):
                    record(cid, state)
        return used

    def _emit_character_images(self, c: Character,
                               used: set[tuple[str, str, str]]) -> list[str]:
        """One `image` per attribute combination the script shows.

        The generator produces a whole sprite per outfit x pose x expression,
        so a layeredimage -- which composites an outfit layer with a separate
        transparent face layer -- was the wrong construct: it looked up keys
        (`outfit/<id>`, `face/<expr>`) that nothing ever wrote, so no generated
        sprite has ever reached an export. Attribute-tagged images are what
        whole-image sprites want, and `show chr_x outfit_a pose_b expr_c`
        resolves against them directly.

        Combinations the script uses but has no art for still get declared, as
        the nearest sprite that does exist or else a Placeholder, so the show
        statements stay exact and readable and the build always runs.
        """
        var = slugify(c.id, "chr")
        have = self._sprite_combos(c)
        placeholder = f'Placeholder("{"girl" if c.gender == "female" else "boy"}")'
        lines: list[str] = []

        for combo in sorted(set(have) | set(used)):
            outfit, pose, expr = combo
            attrs = " ".join([
                slugify(outfit, "outfit"), slugify(pose, "pose"), slugify(expr, "expr"),
            ])
            source = combo if combo in have else self._nearest_combo(c, combo)
            if source is None:
                lines.append(f"image {var} {attrs} = {placeholder}")
                continue
            so, sp, se = source
            stem = f"{slugify(so, 'outfit')}_{slugify(sp, 'pose')}_{slugify(se, 'expr')}"
            lines.append(
                f'image {var} {attrs} = '
                f'{self._scaled(f"images/chars/{var}/{stem}.png", "vn_sprite")}')
            # Several combinations can fall back to the same sprite, so the
            # same file would otherwise be queued for copying more than once.
            entry = {"hash": have[source], "dest": f"game/images/chars/{var}/{stem}.png"}
            if entry not in self.result.assets:
                self.result.assets.append(entry)

        # A bare `show chr_x` has to resolve to something too.
        lines.append(f"image {var} = {placeholder}")
        if not have:
            lines.append(f"# no sprites generated for {c.display_name} yet")
        return lines

    # ------------------------------------------------------------------
    # generated/flags.rpy
    # ------------------------------------------------------------------

    def _emit_flags(self) -> str:
        out = [self._header(), ""]
        flags = merged_flag_defaults(self.project, self.graph.nodes.values(),
                                     self.characters.values())
        out.append("# per-playthrough state")
        for var in sorted(flags):
            out.append(f"default {var} = {py_repr(flags[var])}")

        menusets = sorted(
            {name for name in (_menuset_name(n) for n in self.graph.nodes.values())
             if name}
        )
        if menusets:
            out.append("")
            out.append("# hub scenes: menu (set=...) auto-hides taken options")
            for name in menusets:
                out.append(f"default {name} = set()")

        out.extend(self._route_lines())

        persistents = sorted(
            {
                p
                for n in self.graph.nodes.values()
                if n.type == NodeType.end
                for p in ((n.data or {}).get("unlock_persistent") or [])
            }
        )
        if persistents:
            out.append("")
            out.append("# cross-playthrough unlocks (survive save deletion)")
            out.append("init python:")
            for p in persistents:
                out.append(f"{IND}if persistent.{p} is None:")
                out.append(f"{IND * 2}persistent.{p} = False")
        return "\n".join(out) + "\n"

    def _routes(self) -> dict[str, list[str]]:
        """route name -> its scene labels, in canvas order.

        A route needs no registry of its own: it exists because scenes claim
        it (SceneData.route), and the order is the order the scenes sit on the
        canvas, which is the order the author already reads them in.
        """
        routes: dict[str, list[str]] = {}
        for scene in self.graph.scenes():
            name = ((scene.data or {}).get("route") or "").strip()
            label = self.labels.get(scene.id)
            if name and label:
                routes.setdefault(name, []).append(label)
        return routes

    def _route_lines(self) -> list[str]:
        """The route table plus the lookup a hub option dispatches through.

        `vn_visited` is a store variable rather than `renpy.seen_label`: seen
        is persistent across playthroughs, so a second run would find every
        route already exhausted. Being a store variable it also rolls back
        with saves, so rewinding past a scene un-visits it.
        """
        if not self.routes:
            return []
        out = [
            "",
            "# routes -- a hub option can target 'the next scene in route X",
            "# this playthrough has not reached yet' instead of a fixed node.",
            "default vn_visited = set()",
            "# Scratch for a Jump/Call node dispatching over a route: `jump",
            "# expression` needs a name to evaluate, and a store variable is",
            "# what rolls back with a save.",
            "default vn_next = None",
            "",
            "init python:",
            f"{IND}vn_routes = {{",
        ]
        for name in sorted(self.routes):
            members = ", ".join(f'"{label}"' for label in self.routes[name])
            out.append(f'{IND * 2}"{name}": [{members}],')
        out += [
            f"{IND}}}",
            "",
            f"{IND}def vn_next_in_route(route):",
            f'{IND * 2}"""First scene in `route` this playthrough has not played."""',
            f"{IND * 2}for label in vn_routes.get(route, []):",
            f"{IND * 3}if label not in vn_visited:",
            f"{IND * 4}return label",
            f"{IND * 2}return None",
        ]
        return out

    # ------------------------------------------------------------------
    # generated/theme.rpy
    # ------------------------------------------------------------------

    def _emit_theme(self) -> str:
        """Presentation tokens the hand-owned game/screens.rpy reads.

        Regenerated on every compile so Config -> Presentation stays live.
        Loaded at `init offset = -1` so a redefine in custom.rpy still wins.
        """
        p = self.presentation
        font = (p.get("dialogue_font") or "").strip() or "DejaVuSans.ttf"
        accent = (p.get("accent_color") or "").strip() or "#e0819b"
        out = [
            self._header(),
            "",
            "# Presentation tokens for game/screens.rpy -- from Config -> Presentation.",
            "# Rewritten on every compile. To change any of these by hand, redefine",
            "# them in custom.rpy; it loads after this file and wins.",
            "",
            "init offset = -1",
            "",
            f'define vn.accent = "{accent}"',
            f'define vn.dialogue_font = "{font}"',
            f"define vn.dialogue_size = {int(p.get('text_size') or 33)}",
            f"define vn.textbox_alpha = {_clamp01(p.get('textbox_opacity'), 0.85)}",
            f"define vn.choice_dim = {_clamp01(p.get('choice_dim'), 0.6)}",
            f"define vn.choice_buttons = {_pybool(p.get('choice_buttons', True))}",
            f"define vn.quick_menu = {_pybool(p.get('quick_menu', True))}",
            f"define vn.nvl_narration = {_pybool(p.get('nvl_narration', False))}",
        ]
        cps = int(p.get("text_cps") or 0)
        if cps > 0:
            out.append(f"define config.default_text_cps = {cps}")
        out += ["", "init offset = 0", ""]
        return "\n".join(out) + "\n"

    # ------------------------------------------------------------------
    # generated/scenes/*.rpy
    # ------------------------------------------------------------------

    def _emit_scene_files(self) -> dict[str, str]:
        buckets: dict[str, list[str]] = {}
        order: dict[str, list[str]] = {}

        for node_id in self.labels:
            scene = self.graph.owning_scene(node_id)
            key = slugify(scene.data.get("title", "scene"), "scene") + "_" + scene.id[:6] if scene else "_loose"
            order.setdefault(key, []).append(node_id)

        for key in sorted(order):
            # scene container label first, then the rest in stable id order
            ids = sorted(
                order[key],
                key=lambda nid: (self.graph.nodes[nid].type != NodeType.scene, self.labels[nid]),
            )
            lines = [self._header(), ""]
            for nid in ids:
                lines.extend(self._emit_label_block(nid))
                lines.append("")
            buckets[f"game/generated/scenes/{key}.rpy"] = "\n".join(lines).rstrip() + "\n"
        return buckets

    def _emit_label_block(self, node_id: str) -> list[str]:
        name = self.labels[node_id]
        lines = [f"label {name}:"]
        node = self.graph.nodes.get(node_id)
        if node is not None and node.type == NodeType.scene and (
                (node.data or {}).get("route") or "").strip():
            # What makes "next unplayed scene in this route" answerable.
            lines.append(f'{IND}$ vn_visited.add("{name}")')
        body = self._emit_run(node_id, depth=1, entering_label=True)
        lines.extend(body if body else [IND + "return"])
        return lines

    def _emit_run(self, node_id: str, depth: int, entering_label: bool = False) -> list[str]:
        """Straight-line run: statements until we hit a label or a terminal."""
        out: list[str] = []
        pad = IND * depth
        current: Optional[str] = node_id
        first = True
        guard = 0

        while current and guard < 5000:
            guard += 1
            if not first and current in self.labels:
                out.append(f"{pad}jump {self.labels[current]}")
                return out
            node = self.graph.nodes.get(current)
            if node is None:
                return out
            first = False

            out.extend(self._statements(node, depth))

            if node.type == NodeType.choice:
                out.extend(self._emit_choice(node, depth))
                return out
            if node.type == NodeType.hub:
                out.extend(self._emit_hub(node, depth))
                return out
            if node.type == NodeType.condition:
                out.extend(self._emit_condition(node, depth))
                return out
            if node.type == NodeType.end:
                return out
            if node.type == NodeType.jump:
                out.extend(self._jump_statements(node, depth))
                return out
            if node.type == NodeType.call:
                out.extend(self._call_statements(node, depth))
                nxt = self.graph.successor(node.id, "after")
                if not nxt:
                    out.append(f"{pad}return")
                    return out
                current = nxt
                continue

            nxt = self.graph.default_successor(node.id)
            if not nxt:
                out.append(f"{pad}return")
                return out
            current = nxt
        return out

    # ------------------------------------------------------------------
    # per-node statements
    # ------------------------------------------------------------------

    def _statements(self, node: Node, depth: int) -> list[str]:
        pad = IND * depth
        d = node.data or {}
        t = node.type

        if t == NodeType.scene:
            return self._scene_statements(d, pad)
        if t == NodeType.stage:
            return self._stage_statements(node, d, pad)
        if t == NodeType.say:
            return self._say_statements(node, d, pad)
        if t == NodeType.narration:
            if not d.get("text"):
                return []
            before, after = self._cg_statements(node, d, pad)
            return (self._sfx_statements(d, pad) + before
                    + [f"{pad}{q(d.get('text', ''))}"] + after)
        if t == NodeType.set:
            return self._set_statements(d, pad)
        if t == NodeType.audio:
            return self._audio_statements(d, pad)
        if t == NodeType.transition:
            lines = []
            tr = with_clause(d.get("transition", "dissolve")).strip()
            if tr:
                lines.append(f"{pad}{tr}")
            if d.get("pause"):
                lines.append(f"{pad}pause {float(d['pause'])}")
            return lines
        if t == NodeType.raw:
            # Escape hatch for anything the IR does not model (doc S0).
            return [pad + line if line.strip() else "" for line in (d.get("code", "")).splitlines()]
        if t == NodeType.end:
            lines = [f"{pad}$ persistent.{p} = True" for p in (d.get("unlock_persistent") or [])]
            mode = d.get("mode", "return")
            if mode == "jump" and d.get("ending_id"):
                lines.append(f"{pad}jump {slugify(d['ending_id'], 'ending')}")
            elif mode == "ending" and d.get("ending_id"):
                lines.append(f"{pad}jump ending_{slugify(d['ending_id'], 'x')}")
            else:
                lines.append(f"{pad}return")
            return lines
        return []

    def _scene_statements(self, d: dict[str, Any], pad: str) -> list[str]:
        """Reset to neutral at the top of every Scene container (doc S1)."""
        lines: list[str] = []
        if self.presentation.get("nvl_narration"):
            lines.append(f"{pad}nvl clear")
        bg = self._bg_name(d.get("background"), d.get("time_of_day"))
        lines.append(f"{pad}scene {bg}{with_clause(d.get('transition', 'dissolve'))}")
        if d.get("stop_music"):
            lines.append(f"{pad}stop music fadeout 1.0")
        if d.get("music"):
            lines.append(f'{pad}play music "audio/music/{d["music"]}" fadein 1.0')
        return lines

    def _is_player(self, cid: Optional[str]) -> bool:
        c = self.characters.get(cid or "")
        return bool(c and c.is_player)

    def _stage_statements(self, node: Node, d: dict[str, Any], pad: str) -> list[str]:
        # Music is emitted first but kept out of `lines`, because the trailing
        # `with <transition>` below fires only when something *visual* changed
        # -- a music cue alone must not drag a transition in after it.
        music = self._music_statements(d, pad)
        lines: list[str] = []
        state = resolve_state(self.graph.path_to(node.id), include_last=False)
        # The same test the path state uses to end a held CG, since `scene`
        # is what takes it down.
        if stage_clears(d, state.location):
            bg = self._bg_name(
                d.get("location") or state.location, d.get("time_of_day") or state.time_of_day
            )
            lines.append(f"{pad}scene {bg}{with_clause(d.get('transition', 'dissolve'))}")

        for cid in d.get("hide", []) or []:
            if self._is_player(cid):
                continue
            lines.append(f"{pad}hide {slugify(cid, 'chr')}{with_clause(d.get('transition', 'none'))}")

        after = resolve_state(self.graph.path_to(node.id), include_last=True)
        shown = list(dict.fromkeys((d.get("show") or []) + list((d.get("expressions") or {}).keys())
                                   + list((d.get("outfits") or {}).keys())))
        for cid in shown:
            if cid in (d.get("hide") or []):
                continue
            # The player has no sprite; silently skip any attempt to stage them.
            if self._is_player(cid):
                continue
            lines.append(pad + self._show_statement(cid, after))
        if lines and d.get("transition") not in (None, "", "none") and not d.get("reset"):
            lines.append(f"{pad}with {d['transition']}")
        return music + lines

    def _sfx_statements(self, d: dict[str, Any], pad: str) -> list[str]:
        """One-shot sound for this beat.

        The writer marks the sound as a *description* (`sfx_cue`); a real file only
        arrives later. Emitting `play sound "audio/sfx/knock at the door"` for
        a cue with no file would break the build on a missing asset, so an
        unresolved cue compiles to a comment instead -- visible in the script,
        harmless to Ren'Py, and greppable when sourcing audio.
        """
        cue = (d.get("sfx_cue") or "").strip()
        file = (d.get("sfx_file") or "").strip()
        if file:
            self.result.sound_lines += 1
            return [f'{pad}play sound "audio/sfx/{file}"']
        if cue:
            return [f"{pad}# TODO sfx: {cue}"]
        return []

    def _music_statements(self, d: dict[str, Any], pad: str) -> list[str]:
        """Music change carried by a stage node. Same unresolved-cue rule."""
        if d.get("stop_music"):
            return [f"{pad}stop music fadeout 1.0"]
        file = (d.get("music_file") or "").strip()
        cue = (d.get("music_cue") or "").strip()
        if file:
            return [f'{pad}play music "audio/music/{file}" fadein 1.0']
        if cue:
            return [f"{pad}# TODO music: {cue}"]
        return []

    @staticmethod
    def _cg_name(node: Node) -> str:
        return f"cg_{slugify(node.slug or node.id[:8], 'cg')}"

    def _cg_statements(self, node: Node, d: dict[str, Any], pad: str
                       ) -> tuple[list[str], list[str]]:
        """(before, after) for a line's event CG.

        Shown under a shared `cg` tag so a run of CG nodes replaces rather than
        stacks, above the sprites (CG_ZORDER). Hidden after the line so the
        sprite scene comes back -- unless the node holds it, in which case the
        lines after it play over the picture until something ends the run
        (graph._track_cg).

        A flagged line with no render yet still ends a held run, so it hides
        too. Every hide is emitted whether or not the path walked here holds
        anything: another branch into the same label may, and `hide` on a tag
        that is not showing does nothing.

        Replacing a held CG, or clearing one, dissolves. That is the case of
        two versions of one picture in a row, and a hard cut between them
        reads as a glitch rather than a change.
        """
        if d.get("cg"):
            before: list[str] = []
            if d.get("image_hash"):
                held = resolve_state(self.graph.path_to(node.id), include_last=False).cg_node
                fade = with_clause("dissolve") if held else ""
                before.append(
                    f"{pad}show {self._cg_name(node)} as cg zorder {CG_ZORDER}{fade}")
            return before, ([] if d.get("cg_hold") else [f"{pad}hide cg"])
        if d.get("cg_clear"):
            return [f"{pad}hide cg{with_clause('dissolve')}"], []
        return [], []

    def _say_statements(self, node: Node, d: dict[str, Any], pad: str) -> list[str]:
        # Sound first: it accompanies the beat, so it should fire as the line
        # appears rather than after the player has already read it.
        lines: list[str] = self._sfx_statements(d, pad)
        speaker = d.get("speaker")
        char = self.characters.get(speaker) if speaker else None

        if char and not char.is_player:
            prev = resolve_state(self.graph.path_to(node.id), include_last=False)
            expr = d.get("expression") or "neutral"
            pose = d.get("pose") or "front"
            if (prev.expressions.get(speaker) != expr
                    or prev.poses.get(speaker, "front") != pose
                    or speaker not in prev.present):
                after = resolve_state(self.graph.path_to(node.id), include_last=True)
                lines.append(pad + self._show_statement(speaker, after))

        # The CG goes after the sprite, so clearing a held one dissolves onto
        # the sprite as this line has it, and before the voice, because a
        # dissolve is an interaction of its own and would otherwise be the one
        # the voice starts on.
        before, after = self._cg_statements(node, d, pad)
        lines.extend(before)

        if d.get("voice_path") and char and not char.is_player and char.voice.voiced:
            # voice goes immediately before the say statement (doc S5)
            lines.append(f'{pad}voice "{d["voice_path"]}"')
            self.result.voice_lines += 1

        var = slugify(speaker, "chr") if speaker else None
        text = q(d.get("text", ""))
        lines.append(f"{pad}{var} {text}" if var else f"{pad}{text}")
        return lines + after

    def _effect_statements(self, effects: Any, pad: str) -> list[str]:
        """`{var, op, value}` triples as `$` lines.

        Set nodes, menu options and condition branches all write variables the
        same way, so they all come through here -- a stat that is incremented
        from a branch has to read identically to one incremented from a choice.
        """
        lines = []
        for a in effects or []:
            var, op, value = a.get("var"), a.get("op", "="), a.get("value", "")
            if not var:
                continue
            lines.append(f"{pad}$ {var} {op} {py_literal(value)}")
        return lines

    def _set_statements(self, d: dict[str, Any], pad: str) -> list[str]:
        return self._effect_statements(d.get("assignments"), pad)

    def _audio_statements(self, d: dict[str, Any], pad: str) -> list[str]:
        ch = d.get("channel", "music")
        if d.get("action") == "stop":
            return [f"{pad}stop {ch} fadeout {float(d.get('fadeout', 1.0))}"]
        if not d.get("file"):
            return []
        folder = "music" if ch == "music" else "sfx"
        stmt = f'{pad}{d.get("action", "play")} {ch} "audio/{folder}/{d["file"]}"'
        if d.get("fadein"):
            stmt += f" fadein {float(d['fadein'])}"
        return [stmt]

    def _show_statement(self, cid: str, state) -> str:
        var = slugify(cid, "chr")
        char = self.characters.get(cid)
        # Always the exact state, never a substitute: _emit_character_images
        # declares every combination the script uses, standing in real art
        # where it exists and a Placeholder where it does not.
        outfit = state.outfits.get(cid) or (char.default_outfit if char else "") or ""
        pose = state.poses.get(cid) or "front"
        expr = state.expressions.get(cid) or "neutral"
        stmt = f"show {var} " + " ".join([
            slugify(outfit, "outfit"), slugify(pose, "pose"), slugify(expr, "expr"),
        ])
        pos = state.positions.get(cid)
        if pos:
            stmt += f" at {pos}"
        return stmt

    def _nearest_combo(self, c: Character, want: tuple[str, str, str]):
        """The closest sprite that exists to the one the state asked for.

        A `show` naming attributes with no matching image is a hard Ren'Py
        error, and sprite sets are always partial while a project is being
        built, so the exact combination is a best case rather than a promise.
        Outfit is held first (wrong clothes read as a continuity error),
        then expression, and pose gives way first.
        """
        combos = self._sprite_combos(c)
        if not combos:
            return None
        outfit, pose, expr = want
        for candidate in (
            (outfit, pose, expr),
            (outfit, "front", expr),
            (outfit, pose, "neutral"),
            (outfit, "front", "neutral"),
        ):
            if candidate in combos:
                return candidate
        same_outfit = sorted(k for k in combos if k[0] == outfit)
        if same_outfit:
            return same_outfit[0]
        return sorted(combos)[0]

    def _bg_name(self, location: Optional[str], time_of_day: Optional[str]) -> str:
        if not location:
            return "black"
        base = slugify(location, "loc")
        loc = self.locations.get(location)
        if time_of_day and loc and time_of_day in (loc.variants or {}):
            return f"bg_{base}_{slugify(time_of_day, 'v')}"
        return f"bg_{base}"

    # ------------------------------------------------------------------
    # branching
    # ------------------------------------------------------------------

    def _emit_choice(self, node: Node, depth: int) -> list[str]:
        pad = IND * depth
        d = node.data or {}
        opts = d.get("options") or []
        mode = d.get("mode", "jump")

        head = f"{pad}menu"
        if d.get("exhaust"):
            head += f" (set={d.get('menuset') or 'menuset'})"
        lines = [head + ":"]
        if d.get("prompt"):
            lines.append(f"{IND * (depth + 1)}{q(d['prompt'])}")
        lines.append("")

        if not opts:
            self.result.warnings.append(f"choice {node.id} has no options")
            return [f"{pad}pass"]

        for opt in opts:
            # Picked stat gates and the author's own expression, ANDed. Ren'Py
            # supports an `if` clause directly on a menu option (doc S1).
            cond = self._requires_guard(opt, opt.get("condition") or "")
            suffix = f" if {cond}" if cond else ""
            lines.append(f"{IND * (depth + 1)}{q(opt.get('label', '...'))}{suffix}:")
            target = self.graph.successor(node.id, opt.get("id", ""))
            body_pad = IND * (depth + 2)
            # Variable changes attached to this option fire the moment it is
            # picked, before the branch body runs (item 9).
            lines.extend(self._effect_statements(opt.get("effects"), body_pad))
            if not target:
                self.result.warnings.append(
                    f"choice option '{opt.get('label')}' on {node.id} has no outgoing edge"
                )
                lines.append(f"{body_pad}pass")
            elif target in self.labels:
                lines.append(f"{body_pad}{mode} {self.labels[target]}")
            else:
                lines.extend(self._emit_run(target, depth + 2))
            lines.append("")

        while lines and lines[-1] == "":
            lines.pop()

        if mode == "call" and d.get("loop_back"):
            # hub pattern: return here after every topic (doc S1)
            lines.append(f"{pad}jump {self.labels[node.id]}")
        return lines

    # -- hub -----------------------------------------------------------

    def _hub_condition(self, cond: dict[str, Any]) -> str:
        """One structured `{stat, op, value}` gate as a Ren'Py expression."""
        var = stat_condition_var(cond)
        if not var:
            return ""
        op = (cond.get("op") or ">=").strip()
        return f"{var} {op} {py_literal(str(cond.get('value', '')))}"

    def _requires_guard(self, holder: dict[str, Any], extra: str = "") -> str:
        """Structured gates ANDed with a hand-written expression.

        Choices, hub topics and condition branches all gate the same way; only
        the hub adds a route clause on top. `extra` is the author's own
        expression and is parenthesised, because `a or b` ANDed with a stat
        gate means something else without the brackets.
        """
        parts = [c for c in (self._hub_condition(cond)
                             for cond in (holder.get("requires") or [])) if c]
        extra = (extra or "").strip()
        if extra:
            parts.append(f"({extra})" if parts else extra)
        return " and ".join(parts)

    def _hub_guard(self, node: Node, opt: dict[str, Any]) -> str:
        """Everything that has to be true for this topic to be pickable."""
        parts = [c for c in (self._hub_condition(cond)
                             for cond in (opt.get("requires") or [])) if c]
        route = (opt.get("target_route") or "").strip()
        if route:
            if route not in self.routes:
                self.result.warnings.append(
                    f"hub option '{opt.get('label')}' on {node.id} targets route "
                    f"'{route}', which no scene belongs to")
            # A route with nothing left in it has nowhere to send the player.
            parts.append(f'vn_next_in_route("{route}") is not None')
        return " and ".join(parts)

    def _hub_destination(self, node: Node, opt: dict[str, Any], mode: str,
                         depth: int) -> list[str]:
        pad = IND * depth
        route = (opt.get("target_route") or "").strip()
        if route:
            # Resolved at pick time, not compile time: which scene is "next"
            # depends on what this playthrough has already seen.
            return [f'{pad}{mode} expression vn_next_in_route("{route}")']
        target = self.graph.successor(node.id, opt.get("id", ""))
        if not target:
            self.result.warnings.append(
                f"hub option '{opt.get('label')}' on {node.id} has no outgoing edge"
            )
            return [f"{pad}pass"]
        if target in self.labels:
            return [f"{pad}{mode} {self.labels[target]}"]
        return self._emit_run(target, depth)

    def _emit_hub(self, node: Node, depth: int) -> list[str]:
        """A Choice whose options are gated by declared stats.

        Two things it does that `_emit_choice` cannot. The conditions are
        structured, so the compiler builds the `if` clause rather than passing
        an author's expression through. And a locked option can be *shown*
        greyed instead of hidden: Ren'Py's menu statement has no disabled item,
        so the grey line is emitted as a second entry guarded by the negation,
        whose body drops straight back to the hub -- picking it costs a click
        and changes nothing, which is what greyed means.
        """
        pad = IND * depth
        d = node.data or {}
        opts = d.get("options") or []
        mode = d.get("mode", "call")
        menuset = d.get("menuset") or "hub_topics"
        exhaust = d.get("exhaust", True)
        self_label = self.labels.get(node.id)

        head = f"{pad}menu"
        if exhaust:
            head += f" (set={menuset})"
        lines = [head + ":"]
        if d.get("prompt"):
            lines.append(f"{IND * (depth + 1)}{q(d['prompt'])}")
        lines.append("")

        if not opts:
            self.result.warnings.append(f"hub {node.id} has no options")
            return [f"{pad}pass"]

        opt_pad = IND * (depth + 1)
        body_pad = IND * (depth + 2)
        for opt in opts:
            guard = self._hub_guard(node, opt)
            suffix = f" if {guard}" if guard else ""
            lines.append(f"{opt_pad}{q(opt.get('label', '...'))}{suffix}:")
            if exhaust and opt.get("repeatable"):
                # A menuset remembers by caption, so forgetting the caption on
                # the way through is what keeps a topic permanently available.
                lines.append(
                    f"{body_pad}$ {menuset}.discard({py_repr(opt.get('label', '...'))})")
            lines.extend(self._effect_statements(opt.get("effects"), body_pad))
            lines.extend(self._hub_destination(node, opt, mode, depth + 2))
            lines.append("")

            if guard and opt.get("locked") == "grey":
                locked = (opt.get("locked_label")
                          or f"{opt.get('label', '...')} (locked)")
                lines.append(f"{opt_pad}{q(locked)} if not ({guard}):")
                if exhaust:
                    # A menuset remembers captions, so picking the grey line
                    # would hide it for the rest of the game. Forget it again
                    # on the way back out.
                    lines.append(f"{body_pad}$ {menuset}.discard({py_repr(locked)})")
                lines.append(f"{body_pad}jump {self_label}" if self_label
                             else f"{body_pad}pass")
                lines.append("")

        while lines and lines[-1] == "":
            lines.pop()

        # Only a `call` topic comes back, so only then is there anything after
        # the menu to run -- the same rule the Choice node follows.
        if mode == "call" and d.get("loop_back", True) and self_label:
            lines.append(f"{pad}jump {self_label}")
        return lines

    def _emit_condition(self, node: Node, depth: int) -> list[str]:
        pad = IND * depth
        d = node.data or {}
        branches = d.get("branches") or []
        if not branches:
            nxt = self.graph.default_successor(node.id)
            return self._emit_run(nxt, depth) if nxt else [f"{pad}return"]

        lines: list[str] = []
        keyword = "if"
        emitted_else = False
        for branch in branches:
            expr = self._requires_guard(branch, branch.get("expr") or "")
            target = self.graph.successor(node.id, branch.get("id", ""))
            if expr:
                lines.append(f"{pad}{keyword} {expr}:")
                keyword = "elif"
            else:
                lines.append(f"{pad}else:")
                emitted_else = True
            body_pad = IND * (depth + 1)
            # Effects fire on the way through, before the branch body -- which
            # is what makes "she notices, +5 affection" one node instead of a
            # Set node hanging off every arm.
            effects = self._effect_statements(branch.get("effects"), body_pad)
            lines.extend(effects)
            if not target:
                # `pass` only if the branch body is otherwise empty; the
                # effects are already statements.
                if not effects:
                    lines.append(f"{body_pad}pass")
            elif target in self.labels:
                lines.append(f"{body_pad}jump {self.labels[target]}")
            else:
                lines.extend(self._emit_run(target, depth + 1))
            if emitted_else:
                break

        if not emitted_else and d.get("has_else", True):
            fallthrough = self.graph.successor(node.id, "else")
            lines.append(f"{pad}else:")
            body_pad = IND * (depth + 1)
            if not fallthrough:
                lines.append(f"{body_pad}pass")
            elif fallthrough in self.labels:
                lines.append(f"{body_pad}jump {self.labels[fallthrough]}")
            else:
                lines.extend(self._emit_run(fallthrough, depth + 1))
        return lines

    def _route_dispatch(self, node: Node) -> Optional[str]:
        """The route a Jump/Call dispatches over, if it names a live one.

        A route no scene claims has no table entry, and `vn_next_in_route` is
        only defined when the table exists -- so an unknown route warns and
        falls back to the wired target rather than emitting a call to a
        function that is not there.
        """
        route = ((node.data or {}).get("target_route") or "").strip()
        if not route:
            return None
        if route not in self.routes:
            self.result.warnings.append(
                f"{node.type.value} node {node.id} targets route '{route}', "
                f"which no scene belongs to"
            )
            return None
        return route

    def _jump_statements(self, node: Node, depth: int) -> list[str]:
        """`jump`, either to a fixed label or to the next scene in a route.

        Which scene is "next" depends on what this playthrough has already
        played, so it is resolved when the node is reached, not at compile
        time. A route with nothing left in it falls through to the wired
        target if there is one -- that is what makes "route, then back to the
        hub" a single node instead of a condition wrapped round a jump.
        """
        pad = IND * depth
        fallback = self._jump_target(node)
        route = self._route_dispatch(node)
        if not route:
            return [f"{pad}jump {fallback}" if fallback else f"{pad}return"]
        return [
            f'{pad}$ vn_next = vn_next_in_route("{route}")',
            f"{pad}if vn_next:",
            f"{IND}{pad}jump expression vn_next",
            f"{pad}else:",
            f"{IND}{pad}jump {fallback}" if fallback else f"{IND}{pad}return",
        ]

    def _call_statements(self, node: Node, depth: int) -> list[str]:
        """`call`, either to a fixed label or to the next scene in a route.

        Unlike the jump, an exhausted route is not a dead end: control returns
        here either way, so it simply falls through to the "then" port.
        """
        pad = IND * depth
        target = self._jump_target(node, handle="out")
        route = self._route_dispatch(node)
        if not route:
            return [f"{pad}call {target}"] if target else []
        lines = [
            f'{pad}$ vn_next = vn_next_in_route("{route}")',
            f"{pad}if vn_next:",
            f"{IND}{pad}call expression vn_next",
        ]
        if target:
            lines += [f"{pad}else:", f"{IND}{pad}call {target}"]
        return lines

    def _jump_target(self, node: Node, handle: str = "out") -> Optional[str]:
        d = node.data or {}
        target = self.graph.successor(node.id, handle) or d.get("target_node")
        if target and target in self.labels:
            return self.labels[target]
        if target and target in self.graph.nodes:
            self.result.warnings.append(
                f"{node.type.value} node {node.id} targets un-labelled node {target}"
            )
        if d.get("target_label"):
            return d["target_label"]
        return None

    def _header(self) -> str:
        return (
            "# GENERATED FILE -- do not edit.\n"
            "# Rewritten on every compile from the project IR.\n"
            f"# project: {self.project.name}"
        )


def py_literal(value: str) -> str:
    v = (value or "").strip()
    if v.lower() in ("true", "false"):
        return v.capitalize()
    if v.lower() in ("none", "null"):
        return "None"
    try:
        int(v)
        return v
    except ValueError:
        pass
    try:
        float(v)
        return v
    except ValueError:
        pass
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v
    if v.startswith("[") or v.startswith("{") or v.startswith("("):
        return v
    return '"' + v.replace('"', '\\"') + '"'


def py_repr(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace('"', '\\"') + '"'


def compile_project(
    project: ProjectMeta,
    nodes: list[Node],
    edges: list[Edge],
    characters: list[Character],
    locations: list[Location],
    sprite_size: tuple[int, int] = (832, 1216),
    bg_size: tuple[int, int] = (1344, 768),
    presentation: Optional[dict[str, Any]] = None,
) -> CompileResult:
    return RenpyCompiler(project, nodes, edges, characters, locations,
                         sprite_size, bg_size, presentation).compile()
