"""Script view: a linear editing surface over the same graph.

Two separate mechanisms, on purpose:

**Linearization** walks one path through a scene and returns rows that stay
bound to their node ids. The editor patches those nodes in place, so node UUIDs
-- and therefore Ren'Py labels, and therefore player saves -- never churn just
because someone rewrote a line. This is the primary editing surface.

**Parsing** turns a pasted block of screenplay text into new node specs. It only
ever creates nodes; it never rewrites existing ones. That keeps the round trip
honest: there is no path where re-saving a scene silently renumbers it.

Branching is handled by following one path at a time. A Choice row carries every
option and which one the current path takes, so the view can offer a branch
picker rather than pretending a tree is a line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..compiler.labels import compute_labels
from ..graph import Graph, slugify, stat_condition_var
from ..models import EMOTIONS, EXPRESSIONS, Character, Node, NodeType

# --------------------------------------------------------------------------
# grammar
# --------------------------------------------------------------------------
#   @ Title                     scene header
#   > scene school_rooftop dusk stage directives (consecutive lines merge)
#   > show aiko uniform smiling center
#   > hide bob
#   $ affinity += 1             set
#   ? What do you say?          choice prompt
#   - Push her on it            choice option
#   - Ask about it [knows_letter]   option with a condition
#   AIKO: You came.             say
#   AIKO (looking_away, melancholy 0.6): You came.
#   bare paragraph              narration
#   // anything                 comment, ignored

SAY_RE = re.compile(r"^([^:()\n]{1,48}?)\s*(?:\(([^)]*)\))?\s*:\s*(.*)$")
SET_RE = re.compile(r"^\$\s*([A-Za-z_]\w*)\s*(=|\+=|-=)\s*(.+)$")
OPTION_RE = re.compile(r"^-\s*(.*?)\s*(?:\[([^\]]+)\])?\s*$")
INTENSITY_RE = re.compile(r"^([a-z_]+)\s+([0-9.]+)$")


@dataclass
class ScriptRow:
    """One editable line, bound to the node it came from."""
    node_id: Optional[str]
    kind: str
    text: str = ""
    speaker: Optional[str] = None
    speaker_name: Optional[str] = None
    speaker_color: Optional[str] = None
    expression: str = ""
    emotion: str = ""
    intensity: float = 0.5
    delivery: str = ""
    meta: str = ""
    label: Optional[str] = None
    editable: bool = True
    is_merge: bool = False
    options: list[dict[str, Any]] = field(default_factory=list)
    selected_option: Optional[str] = None
    statuses: dict[str, str] = field(default_factory=dict)
    has_voice: bool = False
    has_image: bool = False
    # The CG itself, not just whether there is one: the Script view renders a
    # thumbnail from it, the same picture the canvas card shows.
    image_hash: Optional[str] = None
    # --- everything below exists so the Script view can do what the canvas
    # inspector can, without a round trip per row to fetch the node itself.
    pose: str = ""
    sfx_cue: str = ""
    shot_hint: str = ""
    cg: bool = False
    cg_hold: bool = False
    cg_clear: bool = False
    notes: str = ""
    pinned_label: bool = False
    # The generation this node came from, if any -- enables "regenerate this
    # block" on the row, which needs to know there *is* a block.
    gen_id: Optional[str] = None
    # Author-facing "where this could go" suggestions from the last generation.
    continuations: list[str] = field(default_factory=list)
    variant_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id, "kind": self.kind, "text": self.text,
            "speaker": self.speaker, "speaker_name": self.speaker_name,
            "speaker_color": self.speaker_color, "expression": self.expression,
            "emotion": self.emotion, "intensity": self.intensity,
            "delivery": self.delivery, "meta": self.meta, "label": self.label,
            "editable": self.editable, "is_merge": self.is_merge,
            "options": self.options, "selected_option": self.selected_option,
            "statuses": self.statuses, "has_voice": self.has_voice,
            "has_image": self.has_image, "image_hash": self.image_hash,
            "pose": self.pose,
            "sfx_cue": self.sfx_cue, "shot_hint": self.shot_hint, "cg": self.cg,
            "cg_hold": self.cg_hold, "cg_clear": self.cg_clear,
            "notes": self.notes, "pinned_label": self.pinned_label,
            "gen_id": self.gen_id, "continuations": self.continuations,
            "variant_count": self.variant_count,
        }


# --------------------------------------------------------------------------
# linearization
# --------------------------------------------------------------------------

def linearize(
    graph: Graph,
    start_id: str,
    characters: dict[str, Character],
    choices: Optional[dict[str, str]] = None,
    limit: int = 600,
) -> dict[str, Any]:
    """Walk one path from `start_id`, returning editable rows.

    `choices` maps a Choice/Condition node id to the handle this path takes.
    Anything unmapped falls through to the first wired branch.
    """
    choices = choices or {}
    labels = compute_labels(graph)
    rows: list[ScriptRow] = []
    seen: set[str] = set()
    taken: dict[str, str] = {}

    current: Optional[str] = start_id
    while current and current in graph.nodes and len(rows) < limit:
        if current in seen:
            rows.append(ScriptRow(node_id=current, kind="loop", editable=False,
                                  meta=f"loops back to {labels.get(current, current[:8])}",
                                  label=labels.get(current)))
            break
        seen.add(current)
        node = graph.nodes[current]
        row = _row_for(node, characters, labels, graph)
        rows.append(row)

        if node.type == NodeType.end:
            break

        if node.type in (NodeType.choice, NodeType.hub, NodeType.condition):
            handle = choices.get(node.id) or _default_handle(graph, node)
            if not handle:
                break
            row.selected_option = handle
            taken[node.id] = handle
            nxt = graph.successor(node.id, handle)
            if not nxt:
                break
            current = nxt
            continue

        if node.type == NodeType.jump:
            nxt = graph.successor(node.id, "out")
            current = nxt
            continue
        if node.type == NodeType.call:
            current = graph.successor(node.id, "after")
            continue

        current = graph.default_successor(node.id)

    return {
        "rows": [r.to_dict() for r in rows],
        "taken": taken,
        "start": start_id,
        "labels": labels,
    }


def _default_handle(graph: Graph, node: Node) -> Optional[str]:
    edges = graph.out_edges(node.id)
    if not edges:
        return None
    d = node.data or {}
    if node.type in (NodeType.choice, NodeType.hub):
        for opt in d.get("options") or []:
            if any(e.source_handle == opt.get("id") for e in edges):
                return opt.get("id")
    if node.type == NodeType.condition:
        for branch in d.get("branches") or []:
            if any(e.source_handle == branch.get("id") for e in edges):
                return branch.get("id")
    return edges[0].source_handle


def _row_for(node: Node, characters: dict[str, Character], labels: dict[str, str],
             graph: Graph) -> ScriptRow:
    d = node.data or {}
    statuses = {"text": _status(node.text_status), "image": _status(node.image_status),
                "audio": _status(node.audio_status)}
    common = {
        "node_id": node.id,
        "label": labels.get(node.id),
        "is_merge": graph.in_degree(node.id) > 1,
        "statuses": statuses,
        "has_voice": bool(d.get("voice_path")),
        "has_image": bool(d.get("image_hash")),
        "image_hash": d.get("image_hash") or None,
        "cg_hold": bool(d.get("cg_hold")),
        "cg_clear": bool(d.get("cg_clear")),
        "notes": node.notes or "",
        "pinned_label": node.pinned_label,
        "gen_id": d.get("_gen"),
        "continuations": list(d.get("_continuations") or []),
        "variant_count": len(node.variants or []),
    }

    if node.type == NodeType.say:
        character = characters.get(d.get("speaker") or "")
        return ScriptRow(kind="say", text=d.get("text", ""),
                         speaker=d.get("speaker"),
                         speaker_name=character.display_name if character else d.get("speaker"),
                         speaker_color=character.color if character else None,
                         expression=d.get("expression", "neutral"),
                         emotion=d.get("emotion", "neutral"),
                         intensity=float(d.get("intensity", 0.5) or 0.5),
                         delivery=d.get("delivery", ""),
                         pose=d.get("pose", "front"),
                         sfx_cue=d.get("sfx_cue", ""),
                         shot_hint=d.get("shot_hint", ""),
                         cg=bool(d.get("cg")), **common)

    if node.type == NodeType.narration:
        return ScriptRow(kind="narration", text=d.get("text", ""),
                         sfx_cue=d.get("sfx_cue", ""),
                         shot_hint=d.get("shot_hint", ""),
                         cg=bool(d.get("cg")), **common)

    if node.type == NodeType.scene:
        meta = " · ".join(x for x in [d.get("background"), d.get("time_of_day"),
                                      d.get("weather")] if x)
        return ScriptRow(kind="scene", text=d.get("title", ""), meta=meta, **common)

    if node.type == NodeType.stage:
        return ScriptRow(kind="stage", text=_stage_text(d), editable=False,
                         meta="stage", **common)

    if node.type == NodeType.set:
        text = "; ".join(f"{a.get('var')} {a.get('op', '=')} {a.get('value')}"
                         for a in (d.get("assignments") or []) if a.get("var"))
        return ScriptRow(kind="set", text=text, editable=False, meta="set", **common)

    if node.type == NodeType.choice:
        options = []
        for opt in d.get("options") or []:
            target = graph.successor(node.id, opt.get("id", ""))
            options.append({
                "id": opt.get("id"), "label": opt.get("label", ""),
                "condition": opt.get("condition", ""),
                "branch_hint": opt.get("branch_hint", ""),
                # Carried through so editing a label in the script view does not
                # drop the option's variable effects (item 9).
                "effects": opt.get("effects", []),
                "target": target, "wired": bool(target),
            })
        return ScriptRow(kind="choice", text=d.get("prompt", ""), options=options,
                         meta=f"{d.get('mode', 'jump')}"
                              + (" · menuset" if d.get("exhaust") else ""), **common)

    if node.type == NodeType.hub:
        options = []
        for opt in d.get("options") or []:
            target = graph.successor(node.id, opt.get("id", ""))
            route = (opt.get("target_route") or "").strip()
            options.append({
                "id": opt.get("id"), "label": opt.get("label", ""),
                # Read-only here: a hub condition is structured, and the row
                # editor has no widget for `{stat, op, value}` -- that lives in
                # the inspector. Rendering it flat still tells you what gates
                # the topic while you read the script.
                "condition": _hub_requirement_text(opt),
                "branch_hint": opt.get("branch_hint", ""),
                "effects": opt.get("effects", []),
                "target": target, "wired": bool(target) or bool(route),
            })
        meta = f"hub · {d.get('mode', 'call')}"
        if d.get("exhaust"):
            meta += " · menuset"
        if d.get("loop_back"):
            meta += " · loops"
        return ScriptRow(kind="hub", text=d.get("prompt", ""), options=options,
                         editable=False, meta=meta, **common)

    if node.type == NodeType.condition:
        options = []
        for branch in d.get("branches") or []:
            target = graph.successor(node.id, branch.get("id", ""))
            options.append({
                "id": branch.get("id"), "label": branch.get("expr") or "else",
                "condition": branch.get("expr", ""), "target": target,
                "wired": bool(target),
            })
        return ScriptRow(kind="condition", text="", options=options, editable=False,
                         meta="if / else", **common)

    if node.type == NodeType.end:
        mode = d.get("mode", "return")
        return ScriptRow(kind="end", editable=False,
                         text="return" if mode == "return" else f"→ {d.get('ending_id', '')}",
                         meta="end", **common)

    if node.type == NodeType.audio:
        return ScriptRow(kind="audio", editable=False,
                         text=f"{d.get('action', 'play')} {d.get('channel', 'music')} "
                              f"{d.get('file') or ''}".strip(), meta="audio", **common)

    if node.type == NodeType.raw:
        return ScriptRow(kind="raw", text=d.get("code", ""), meta="raw Ren'Py", **common)

    return ScriptRow(kind=node.type.value, text="", editable=False,
                     meta=node.type.value, **common)


def _hub_requirement_text(opt: dict[str, Any]) -> str:
    """A hub option's structured gates, flattened for reading."""
    parts: list[str] = []
    for cond in opt.get("requires") or []:
        var = stat_condition_var(cond)
        if var:
            parts.append(f"{var} {cond.get('op') or '>='} {cond.get('value', '')}".strip())
    route = (opt.get("target_route") or "").strip()
    if route:
        parts.append(f"→ next unplayed in route '{route}'")
    return " and ".join(parts)


def _status(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _stage_text(d: dict[str, Any]) -> str:
    bits: list[str] = []
    if d.get("location"):
        bits.append(f"scene {d['location']}"
                    + (f" {d['time_of_day']}" if d.get("time_of_day") else ""))
    elif d.get("time_of_day"):
        bits.append(str(d["time_of_day"]))
    if d.get("weather"):
        bits.append(str(d["weather"]))
    for cid in d.get("show") or []:
        parts = [cid]
        if (d.get("outfits") or {}).get(cid):
            parts.append(d["outfits"][cid])
        if (d.get("expressions") or {}).get(cid):
            parts.append(d["expressions"][cid])
        if (d.get("positions") or {}).get(cid):
            parts.append(d["positions"][cid])
        bits.append("show " + " ".join(parts))
    for cid in d.get("hide") or []:
        bits.append(f"hide {cid}")
    # A music change is carried by a stage node and is often the only thing on
    # it. Left out of the summary the row reads "(no changes)", which is how a
    # working node comes to look like a bug.
    if d.get("stop_music"):
        bits.append("stop music")
    elif d.get("music_file") or d.get("music_cue"):
        bits.append(f"music {d.get('music_file') or d['music_cue']}")
    return " · ".join(bits) or "(no changes)"


# --------------------------------------------------------------------------
# text emission (copy out, and the round-trip reference)
# --------------------------------------------------------------------------

def emit_script(rows: list[dict[str, Any]] | list[ScriptRow]) -> str:
    """Render rows as script text. Inverse of `parse_script`."""
    out: list[str] = []
    for raw in rows:
        row = raw if isinstance(raw, dict) else raw.to_dict()
        kind = row.get("kind")

        if kind == "scene":
            out.append(f"@ {row.get('text', '')}".rstrip())
            out.append("")
        elif kind == "say":
            speaker = row.get("speaker") or "???"
            out.append(f"{speaker.upper()}{_attrs(row)}: {row.get('text', '')}")
            out.append("")
        elif kind == "narration":
            out.append(row.get("text", ""))
            out.append("")
        elif kind == "stage":
            for bit in (row.get("text") or "").split(" · "):
                if bit and bit != "(no changes)":
                    out.append(f"> {bit}")
            out.append("")
        elif kind == "set":
            for bit in (row.get("text") or "").split("; "):
                if bit:
                    out.append(f"$ {bit}")
            out.append("")
        elif kind == "choice":
            out.append(f"? {row.get('text', '')}".rstrip())
            for opt in row.get("options") or []:
                condition = f" [{opt['condition']}]" if opt.get("condition") else ""
                out.append(f"- {opt.get('label', '')}{condition}")
            out.append("")
        elif kind == "raw":
            out.append("```")
            out.extend((row.get("text") or "").splitlines())
            out.append("```")
            out.append("")

    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def _attrs(row: dict[str, Any]) -> str:
    expression = row.get("expression") or "neutral"
    emotion = row.get("emotion") or "neutral"
    intensity = float(row.get("intensity", 0.5) or 0.5)
    if expression == "neutral" and emotion == "neutral" and abs(intensity - 0.5) < 1e-6:
        return ""
    parts = []
    if expression and expression != "neutral":
        parts.append(expression)
    if emotion and emotion != "neutral":
        parts.append(f"{emotion} {_trim(intensity)}")
    elif abs(intensity - 0.5) > 1e-6:
        parts.append(f"neutral {_trim(intensity)}")
    return f" ({', '.join(parts)})" if parts else ""


def _trim(value: float) -> str:
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text or "0"


# --------------------------------------------------------------------------
# parsing (bulk paste -> new nodes)
# --------------------------------------------------------------------------

def parse_script(text: str, characters: dict[str, Character],
                 locations: Optional[set[str]] = None) -> dict[str, Any]:
    """Turn pasted screenplay text into node specs.

    Only ever produces *new* nodes. Unknown speakers become narration rather
    than inventing a character, and every guess is reported as a warning.
    """
    locations = locations or set()
    by_name: dict[str, str] = {}
    for cid, character in characters.items():
        by_name[cid.lower()] = cid
        by_name[character.display_name.lower()] = cid

    specs: list[dict[str, Any]] = []
    warnings: list[str] = []
    paragraph: list[str] = []
    pending_stage: list[str] = []
    pending_choice: Optional[dict[str, Any]] = None
    in_raw = False
    raw_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            specs.append({"type": "narration", "data": {"text": " ".join(paragraph).strip()}})
            paragraph = []

    def flush_stage() -> None:
        nonlocal pending_stage
        if pending_stage:
            specs.append({"type": "stage", "data": _parse_stage(pending_stage, by_name)})
            pending_stage = []

    def flush_choice() -> None:
        nonlocal pending_choice
        if pending_choice:
            if len(pending_choice["data"]["options"]) < 2:
                warnings.append(
                    f"choice '{pending_choice['data']['prompt']}' has fewer than two options")
            specs.append(pending_choice)
            pending_choice = None

    def flush_all() -> None:
        flush_paragraph()
        flush_stage()
        flush_choice()

    for lineno, raw_line in enumerate((text or "").replace("\r\n", "\n").split("\n"), 1):
        line = raw_line.rstrip()

        if line.strip().startswith("```"):
            if in_raw:
                specs.append({"type": "raw", "data": {"code": "\n".join(raw_lines)}})
                raw_lines = []
            else:
                flush_all()
            in_raw = not in_raw
            continue
        if in_raw:
            raw_lines.append(raw_line)
            continue

        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            flush_stage()
            continue
        if stripped.startswith("//") or stripped.startswith("#"):
            continue

        if stripped.startswith("@"):
            flush_all()
            specs.append({"type": "scene", "data": {"title": stripped[1:].strip() or "Untitled"}})
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            flush_choice()
            pending_stage.append(stripped[1:].strip())
            continue

        set_match = SET_RE.match(stripped)
        if set_match:
            flush_all()
            specs.append({"type": "set", "data": {"assignments": [{
                "var": set_match.group(1), "op": set_match.group(2),
                "value": set_match.group(3).strip(),
            }]}})
            continue

        if stripped.startswith("?"):
            flush_all()
            pending_choice = {"type": "choice", "data": {
                "prompt": stripped[1:].strip(), "options": [], "mode": "jump",
                "exhaust": False, "menuset": "menuset", "loop_back": False,
            }}
            continue

        if stripped.startswith("-") and pending_choice:
            option = OPTION_RE.match(stripped)
            if option:
                index = len(pending_choice["data"]["options"]) + 1
                pending_choice["data"]["options"].append({
                    "id": f"o{index}", "label": option.group(1),
                    "condition": (option.group(2) or "").strip(), "branch_hint": "",
                })
            continue

        say = SAY_RE.match(stripped)
        if say:
            name = say.group(1).strip()
            speaker = by_name.get(name.lower())
            if speaker:
                flush_all()
                data = {"speaker": speaker, "text": say.group(3).strip(),
                        "expression": "neutral", "emotion": "neutral", "intensity": 0.5,
                        "delivery": "", "shot_hint": ""}
                attrs, attr_warnings = _parse_attrs(say.group(2), lineno)
                data.update(attrs)
                warnings.extend(attr_warnings)
                specs.append({"type": "say", "data": data})
                continue
            # A colon in ordinary prose is not a speaker cue.
            if name.isupper() and len(name.split()) <= 3:
                warnings.append(
                    f"line {lineno}: '{name}' is not a known character — kept as narration")

        paragraph.append(stripped)

    if in_raw and raw_lines:
        specs.append({"type": "raw", "data": {"code": "\n".join(raw_lines)}})
    flush_all()

    return {"nodes": specs, "warnings": warnings, "count": len(specs)}


def _parse_attrs(blob: Optional[str], lineno: int) -> tuple[dict[str, Any], list[str]]:
    out: dict[str, Any] = {}
    warnings: list[str] = []
    if not blob:
        return out, warnings
    for token in blob.split(","):
        token = token.strip().lower()
        if not token:
            continue
        match = INTENSITY_RE.match(token)
        if match and match.group(1) in EMOTIONS:
            out["emotion"] = match.group(1)
            out["intensity"] = max(0.0, min(1.0, float(match.group(2))))
        elif token in EXPRESSIONS:
            out["expression"] = token
        elif token in EMOTIONS:
            out["emotion"] = token
        else:
            out["delivery"] = (out.get("delivery", "") + " " + token).strip()
            warnings.append(
                f"line {lineno}: '{token}' is not a known expression or emotion — "
                "kept as a delivery note")
    return out, warnings


def _parse_stage(lines: list[str], by_name: dict[str, str]) -> dict[str, Any]:
    data: dict[str, Any] = {
        "location": None, "time_of_day": None, "show": [], "hide": [],
        "outfits": {}, "expressions": {}, "positions": {}, "reset": False,
    }
    positions = {"left", "center", "right"}
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        verb = parts[0].lower()
        if verb == "scene":
            if len(parts) > 1:
                data["location"] = parts[1]
            if len(parts) > 2:
                data["time_of_day"] = parts[2]
            data["reset"] = True
        elif verb == "show" and len(parts) > 1:
            cid = by_name.get(parts[1].lower(), parts[1])
            if cid not in data["show"]:
                data["show"].append(cid)
            for extra in parts[2:]:
                if extra.lower() in positions:
                    data["positions"][cid] = extra.lower()
                elif extra in EXPRESSIONS:
                    data["expressions"][cid] = extra
                else:
                    data["outfits"][cid] = extra
        elif verb == "hide" and len(parts) > 1:
            data["hide"].append(by_name.get(parts[1].lower(), parts[1]))
    return data
