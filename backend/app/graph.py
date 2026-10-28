"""Graph algorithms over the IR.

Two things in here decide whether the compiler is correct (doc S9):
label computation (merge points) and stable label naming.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .models import Edge, Node, NodeType

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str = "node") -> str:
    s = _SLUG_RE.sub("_", (text or "").lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    if not s:
        s = fallback
    if s[0].isdigit():
        s = "n_" + s
    return s[:40]


def short_hash(value: str, n: int = 8) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:n]


def label_name(node: Node) -> str:
    """Deterministic and stable across recompiles (doc S1).

    slug + short hash of the node UUID -- never a positional index, because a
    label shift breaks every existing player save.
    """
    prefix = "sc" if node.type == NodeType.scene else "lb"
    base = node.slug or _auto_slug(node)
    return f"{prefix}_{slugify(base)}_{short_hash(node.id)}"


def _auto_slug(node: Node) -> str:
    d = node.data or {}
    if node.type == NodeType.scene:
        return slugify(d.get("title") or "scene", "scene")
    if node.type == NodeType.choice:
        return slugify(d.get("prompt") or "choice", "choice")
    if node.type == NodeType.hub:
        return slugify(d.get("prompt") or "hub", "hub")
    if node.type in (NodeType.say, NodeType.narration):
        return slugify((d.get("text") or "")[:40] or node.type.value, node.type.value)
    if node.type == NodeType.end:
        return slugify(d.get("ending_id") or "end", "end")
    return node.type.value


@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge]
    _out: dict[str, list[Edge]] = field(default_factory=dict)
    _in: dict[str, list[Edge]] = field(default_factory=dict)

    @classmethod
    def build(cls, nodes: Iterable[Node], edges: Iterable[Edge]) -> "Graph":
        node_map = {n.id: n for n in nodes if not n.orphaned}
        edge_list = [e for e in edges if e.source in node_map and e.target in node_map]
        g = cls(nodes=node_map, edges=edge_list)
        for e in edge_list:
            g._out.setdefault(e.source, []).append(e)
            g._in.setdefault(e.target, []).append(e)
        for bucket in g._out.values():
            bucket.sort(key=lambda e: (e.source_handle, e.id))
        for bucket in g._in.values():
            bucket.sort(key=lambda e: (e.source, e.source_handle, e.id))
        return g

    # -- adjacency -------------------------------------------------------

    def out_edges(self, node_id: str) -> list[Edge]:
        return self._out.get(node_id, [])

    def in_edges(self, node_id: str) -> list[Edge]:
        return self._in.get(node_id, [])

    def in_degree(self, node_id: str) -> int:
        return len(self._in.get(node_id, []))

    def successor(self, node_id: str, handle: str = "out") -> Optional[str]:
        for e in self.out_edges(node_id):
            if e.source_handle == handle:
                return e.target
        return None

    def default_successor(self, node_id: str) -> Optional[str]:
        edges = self.out_edges(node_id)
        return edges[0].target if edges else None

    def scenes(self) -> list[Node]:
        return sorted(
            (n for n in self.nodes.values() if n.type == NodeType.scene),
            key=lambda n: (n.y, n.x, n.id),
        )

    def roots(self) -> list[Node]:
        """Nodes with no inbound edge -- entry points into the story."""
        return sorted(
            (n for n in self.nodes.values() if self.in_degree(n.id) == 0),
            key=lambda n: (n.y, n.x, n.id),
        )

    # -- labels ----------------------------------------------------------

    def label_nodes(self) -> dict[str, str]:
        """Emit a label only where it is needed (doc S1 'Label emission rules').

        1. Scene container start
        2. Any choice-branch target
        3. Merge points -- in-degree > 1
        4. Nodes the user explicitly pinned
        Everything else is a straight-line run inside the parent label.
        """
        labels: dict[str, str] = {}

        def mark(node_id: str) -> None:
            node = self.nodes.get(node_id)
            if node is not None and node_id not in labels:
                labels[node_id] = label_name(node)

        for node in self.nodes.values():
            if node.type == NodeType.scene:
                mark(node.id)
            # A hub is always a label: `loop_back` returns to it, and a greyed
            # option re-shows it, so it needs a name to jump to even when
            # nothing else in the graph points at it twice.
            if node.type == NodeType.hub:
                mark(node.id)
            if node.pinned_label:
                mark(node.id)
            if self.in_degree(node.id) > 1:
                mark(node.id)
            # An unreachable non-scene root still needs somewhere to live.
            if self.in_degree(node.id) == 0 and node.type != NodeType.scene:
                mark(node.id)

        for node in self.nodes.values():
            # Choice and Call branch targets always become labels: a `menu`
            # option body has to end in a jump/call to a real label.
            if node.type in (NodeType.choice, NodeType.hub):
                for e in self.out_edges(node.id):
                    mark(e.target)
            if node.type == NodeType.call:
                for e in self.out_edges(node.id):
                    mark(e.target)
            if node.type == NodeType.jump:
                for e in self.out_edges(node.id):
                    mark(e.target)

        # A label must be unique. Collisions can only come from a hash clash,
        # but resolve deterministically anyway rather than silently merging.
        seen: dict[str, str] = {}
        for node_id in sorted(labels):
            name = labels[node_id]
            if name in seen:
                name = f"{name}_{short_hash(node_id + name, 4)}"
                labels[node_id] = name
            seen[name] = node_id
        return labels

    # -- paths -----------------------------------------------------------

    def path_to(self, node_id: str, limit: int = 400) -> list[Node]:
        """Walk *up* the graph to the root (doc S2 'Context assembly').

        Context is the path from root to the insertion node, not a chat log --
        this is what makes branch-aware regeneration correct. At a merge point
        we take the deterministic first predecessor.
        """
        chain: list[Node] = []
        seen: set[str] = set()
        current = node_id
        while current and current in self.nodes and current not in seen and len(chain) < limit:
            seen.add(current)
            chain.append(self.nodes[current])
            incoming = self.in_edges(current)
            current = incoming[0].source if incoming else None
        chain.reverse()
        return chain

    def subtree(self, node_id: str, limit: int = 2000) -> list[str]:
        """Every node reachable downstream, for regenerate-from-node."""
        out: list[str] = []
        seen = {node_id}
        stack = [node_id]
        while stack and len(out) < limit:
            cur = stack.pop()
            for e in self.out_edges(cur):
                if e.target not in seen:
                    seen.add(e.target)
                    out.append(e.target)
                    stack.append(e.target)
        return out

    def strict_subtree(self, node_id: str) -> list[str]:
        """Downstream nodes reachable *only* through node_id.

        A merge point still fed by another live branch is not included, so
        "delete everything below" never tears out a shared later scene (item 12).
        """
        candidates = set(self.subtree(node_id))
        if not candidates:
            return []
        keep: set[str] = set()
        frontier: list[str] = []
        for other in self.nodes:
            if other == node_id or other in candidates:
                continue
            for e in self.out_edges(other):
                if e.target in candidates and e.target not in keep:
                    keep.add(e.target)
                    frontier.append(e.target)
        while frontier:
            cur = frontier.pop()
            for e in self.out_edges(cur):
                if e.target in candidates and e.target not in keep:
                    keep.add(e.target)
                    frontier.append(e.target)
        return sorted(candidates - keep)

    def owning_scene(self, node_id: str) -> Optional[Node]:
        """The Scene container a node compiles into."""
        node = self.nodes.get(node_id)
        if node is None:
            return None
        if node.type == NodeType.scene:
            return node
        if node.parent_id and node.parent_id in self.nodes:
            return self.nodes[node.parent_id]
        for anc in reversed(self.path_to(node_id)):
            if anc.type == NodeType.scene:
                return anc
        return None


# --------------------------------------------------------------------------
# path state
# --------------------------------------------------------------------------

@dataclass
class PathState:
    """Resolved 'where are we, who is here, what are they wearing'.

    Computed by walking root->node, so any node knows its own state without
    per-node duplication (doc S4 'Outfits and where clothing lives').
    """
    location: Optional[str] = None
    time_of_day: Optional[str] = None
    weather: Optional[str] = None
    present: list[str] = field(default_factory=list)
    outfits: dict[str, str] = field(default_factory=dict)
    expressions: dict[str, str] = field(default_factory=dict)
    poses: dict[str, str] = field(default_factory=dict)
    positions: dict[str, str] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)
    music: Optional[str] = None
    scene_title: Optional[str] = None
    # The event CG still on screen after this point, and the node it belongs
    # to. Only a held CG survives its own line (`cg_hold`), so this is None
    # everywhere except inside a held run.
    cg_node: Optional[str] = None
    cg_image: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "time_of_day": self.time_of_day,
            "weather": self.weather,
            "present": list(self.present),
            "outfits": dict(self.outfits),
            "expressions": dict(self.expressions),
            "poses": dict(self.poses),
            "positions": dict(self.positions),
            "flags": dict(self.flags),
            "music": self.music,
            "scene_title": self.scene_title,
            "cg_node": self.cg_node,
            "cg_image": self.cg_image,
        }


def _coerce(value: str) -> Any:
    v = (value or "").strip()
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v.strip("\"'")


def resolve_state(path: list[Node], include_last: bool = True) -> PathState:
    state = PathState()
    nodes = path if include_last else path[:-1]
    for node in nodes:
        d = node.data or {}
        if node.type == NodeType.scene:
            state.scene_title = d.get("title")
            # `scene` clears the master layer, so everyone leaves the stage --
            # a held CG included.
            state.cg_node = state.cg_image = None
            state.location = d.get("background") or state.location
            state.time_of_day = d.get("time_of_day") or state.time_of_day
            state.weather = d.get("weather") or state.weather
            state.present = []
            state.expressions = {}
            state.poses = {}
            state.positions = {}
            if d.get("music"):
                state.music = d.get("music")
            if d.get("stop_music"):
                state.music = None
        elif node.type == NodeType.stage:
            if stage_clears(d, state.location):
                state.cg_node = state.cg_image = None
            if d.get("reset"):
                state.present = []
                state.expressions = {}
                state.poses = {}
                state.positions = {}
            state.location = d.get("location") or state.location
            state.time_of_day = d.get("time_of_day") or state.time_of_day
            state.weather = d.get("weather") or state.weather
            for cid in d.get("show", []) or []:
                if cid not in state.present:
                    state.present.append(cid)
            for cid in d.get("hide", []) or []:
                if cid in state.present:
                    state.present.remove(cid)
            state.outfits.update(d.get("outfits", {}) or {})
            state.expressions.update(d.get("expressions", {}) or {})
            state.poses.update(d.get("poses", {}) or {})
            state.positions.update(d.get("positions", {}) or {})
            # Music is state and this is the state-mutation node, so a stage
            # node carries it the same way a scene node does.
            if d.get("stop_music"):
                state.music = None
            elif d.get("music_file") or d.get("music_cue"):
                state.music = d.get("music_file") or d.get("music_cue")
        elif node.type == NodeType.say:
            speaker = d.get("speaker")
            if speaker:
                if speaker not in state.present:
                    state.present.append(speaker)
                if d.get("expression"):
                    state.expressions[speaker] = d["expression"]
                if d.get("pose"):
                    state.poses[speaker] = d["pose"]
            _track_cg(state, node)
        elif node.type == NodeType.narration:
            _track_cg(state, node)
        elif node.type == NodeType.set:
            for a in d.get("assignments", []) or []:
                var, op, raw = a.get("var"), a.get("op", "="), a.get("value", "")
                if not var:
                    continue
                value = _coerce(raw)
                current = state.flags.get(var, 0)
                if op == "=":
                    state.flags[var] = value
                elif op == "+=" and isinstance(current, (int, float)) and isinstance(value, (int, float)):
                    state.flags[var] = current + value
                elif op == "-=" and isinstance(current, (int, float)) and isinstance(value, (int, float)):
                    state.flags[var] = current - value
                else:
                    state.flags[var] = value
        elif node.type == NodeType.audio:
            if d.get("channel") == "music":
                state.music = d.get("file") if d.get("action") == "play" else None
    return state


def stage_clears(d: dict[str, Any], location: Optional[str]) -> bool:
    """Whether a Stage node compiles to a `scene` statement -- a reset, or a
    move to somewhere other than `location`, where the stage already is.

    `scene` wipes the master layer, so this is also where a held CG ends. One
    definition, because the Ren'Py compiler emits the statement off it and the
    path state has to agree about what is left on screen.
    """
    return bool(d.get("reset") or (d.get("location") and d.get("location") != location))


def _track_cg(state: PathState, node: Node) -> None:
    """Advance the held CG past one say or narration line.

    A line flagged for a CG takes the slot whether or not it has been rendered
    yet: it holds or it does not, and which picture sits in the slot is the
    only thing the render changes. So an unrendered held CG keeps the one
    before it on screen as a stand-in, an unrendered unheld one ends the run,
    and rendering it later never moves where the run ends.
    """
    d = node.data or {}
    if d.get("cg"):
        if not d.get("cg_hold"):
            state.cg_node = state.cg_image = None
        elif d.get("image_hash"):
            state.cg_node, state.cg_image = node.id, d["image_hash"]
    elif d.get("cg_clear"):
        state.cg_node = state.cg_image = None


def _note_flag(flags: dict[str, Any], assignment: dict[str, Any]) -> None:
    var = assignment.get("var")
    if not var:
        return
    value = _coerce(assignment.get("value", ""))
    if assignment.get("op") in ("+=", "-="):
        flags.setdefault(var, 0)
    elif isinstance(value, bool):
        flags.setdefault(var, False)
    elif isinstance(value, (int, float)):
        flags.setdefault(var, 0)
    else:
        flags.setdefault(var, "")


def collect_flags(nodes: Iterable[Node]) -> dict[str, Any]:
    """Every variable a Set node, a menu option or a condition branch touches,
    for `default` emission (doc S1).

    Anything that can be written has to be listed here or Ren'Py raises on the
    first read of a name nothing declared.
    """
    flags: dict[str, Any] = {}
    for node in nodes:
        if node.type == NodeType.set:
            for a in (node.data or {}).get("assignments", []) or []:
                _note_flag(flags, a)
        elif node.type in (NodeType.choice, NodeType.hub):
            for opt in (node.data or {}).get("options", []) or []:
                for a in opt.get("effects", []) or []:
                    _note_flag(flags, a)
        elif node.type == NodeType.condition:
            for branch in (node.data or {}).get("branches", []) or []:
                for a in branch.get("effects", []) or []:
                    _note_flag(flags, a)
    return flags


def _attr(obj: Any, name: str, fallback: Any = None) -> Any:
    """Read a field off either a pydantic model or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name, fallback)
    return getattr(obj, name, fallback)


def _typed_default(vtype: Optional[str], raw: Any) -> Any:
    if vtype == "int":
        try:
            return int(str(raw).strip() or 0)
        except (TypeError, ValueError):
            return 0
    if vtype == "str":
        return str(raw or "")
    return str(raw).strip().lower() in ("true", "1", "yes")


def variable_defaults(variables: Iterable[Any]) -> dict[str, Any]:
    """The project's declared variables, as concrete default values."""
    out: dict[str, Any] = {}
    for v in variables or []:
        name = _attr(v, "name")
        if not name:
            continue
        out[name] = _typed_default(_attr(v, "type", "bool"), _attr(v, "default", ""))
    return out


def stat_var(stat_id: str, character_id: str) -> str:
    """The variable one character stat compiles to. One definition, because the
    compiler, the context assembler and the structuring schema must agree."""
    return f"{stat_id}_{character_id}"


def stat_condition_var(cond: Any) -> str:
    """The variable one structured Hub condition tests.

    A per-character stat compiles to `{stat}_{character}` like everywhere else;
    with no character the `stat` field is already a plain variable name.
    """
    stat = (_attr(cond, "stat", "") or "").strip()
    character = (_attr(cond, "character", "") or "").strip()
    if not stat:
        return ""
    return stat_var(stat, character) if character else stat


def stat_variable_infos(project: Any, characters: Iterable[Any]) -> list[dict[str, Any]]:
    """Every `{stat}_{character}` instance, described well enough to pick from.

    The stat schema is declared once and the variables it implies are never
    typed by anyone, so nothing would list `affection_diana` in a dropdown
    without this. `label` is what a picker shows ("Diana — Affection");
    `stat_id`/`character_id` let a structured gate be rebuilt from a bare
    variable name.
    """
    out: list[dict[str, Any]] = []
    stats = _attr(project, "character_stats") or []
    people = list(characters or [])
    for stat in stats:
        sid = _attr(stat, "id")
        if not sid:
            continue
        stype = _attr(stat, "type", "int")
        sname = _attr(stat, "name") or sid
        base = _attr(stat, "default", "")
        for c in people:
            cid = _attr(c, "id")
            if not cid:
                continue
            who = _attr(c, "display_name") or cid
            raw = (_attr(c, "stat_overrides") or {}).get(sid, base)
            out.append({
                "name": stat_var(sid, cid),
                "type": stype,
                "default": str(raw),
                "note": _attr(stat, "note", "") or "",
                "derived": True,
                "source": "stat",
                "stat_id": sid,
                "character_id": cid,
                "label": f"{who} — {sname}",
            })
    return out


def stat_defaults(project: Any, characters: Iterable[Any]) -> dict[str, Any]:
    """Every `{stat}_{character}` variable, at its starting value.

    A character's own `stat_overrides` wins over the StatDef default, so a
    childhood friend can start at affection 30 without a Set node in the
    opening scene.
    """
    out: dict[str, Any] = {}
    stats = _attr(project, "character_stats") or []
    people = list(characters or [])
    for stat in stats:
        sid = _attr(stat, "id")
        if not sid:
            continue
        stype = _attr(stat, "type", "int")
        base = _attr(stat, "default", "")
        for c in people:
            cid = _attr(c, "id")
            if not cid:
                continue
            raw = (_attr(c, "stat_overrides") or {}).get(sid, base)
            out[stat_var(sid, cid)] = _typed_default(stype, raw)
    return out


def merged_flag_defaults(project: Any, nodes: Iterable[Node],
                         characters: Iterable[Any] = ()) -> dict[str, Any]:
    """Character stats, then declared variables, then usage-inferred vars.

    Declared variables deliberately win over stats: a project that already
    hand-declared `affection_diana` before stats existed keeps its own default
    rather than being silently reset by the schema.
    """
    flags = stat_defaults(project, characters)
    flags.update(variable_defaults(_attr(project, "variables") or []))
    for var, value in collect_flags(nodes).items():
        flags.setdefault(var, value)
    return flags


def text_hash(node: Node) -> str:
    """Content hash used for staleness tracking (doc S6)."""
    d = node.data or {}
    parts = [
        node.type.value,
        str(d.get("text", "")),
        str(d.get("speaker", "")),
        str(d.get("expression", "")),
        str(d.get("emotion", "")),
        str(d.get("intensity", "")),
        str(d.get("delivery", "")),
        str(d.get("shot_hint", "")),
        # Editing the sound cue has to invalidate whatever audio was resolved
        # from it, the same way editing the text invalidates the voice line.
        str(d.get("sfx_cue", "")),
    ]
    return hashlib.sha256("␟".join(parts).encode("utf-8")).hexdigest()[:16]


def upstream_hash(graph: Graph, node_id: str) -> str:
    """Hash of all upstream node contents -- the rolling-summary cache key."""
    chain = graph.path_to(node_id)
    joined = "|".join(f"{n.id}:{text_hash(n)}" for n in chain)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]
