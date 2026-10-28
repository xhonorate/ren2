"""Load a project directory, validate it, and assemble context packs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import ValidationError

from .schema import (CG, Affection, Appearance, CGEnd, Choice, Enter, Exit, If, Jump, Nsfw, Outline, Say,
                     Scene, SceneError, SetFlags, Stage, Step, compile_condition, condition_refs, parse_steps, walk)
from .vocab import Vocab

SPECIAL_SPEAKERS = {"narrator", "player"}


@dataclass
class Character:
    id: str
    dir: Path
    appearance: Appearance
    sheet: str = ""
    voice: dict[str, Any] = field(default_factory=dict)


@dataclass
class Location:
    id: str
    description: str
    times: list[str]
    bg_prompt: str


@dataclass
class Project:
    root: Path
    premise: str
    style: str
    world: str
    locations: dict[str, Location]
    vocab: Vocab
    characters: dict[str, Character]
    outline: Outline
    ledger: list[dict[str, Any]]

    # ---- loading --------------------------------------------------------

    @classmethod
    def load(cls, root: Path | str) -> "Project":
        root = Path(root)
        if not (root / "outline.yaml").exists():
            raise FileNotFoundError(f"{root} is not a project (no outline.yaml)")
        read = lambda name: (root / name).read_text(encoding="utf-8") if (root / name).exists() else ""
        world = read("world.md")
        with open(root / "outline.yaml", encoding="utf-8") as f:
            outline = Outline.model_validate(yaml.safe_load(f) or {})
        ledger: list = []
        if (root / "ledger.yaml").exists():
            with open(root / "ledger.yaml", encoding="utf-8") as f:
                ledger = yaml.safe_load(f) or []
        characters: dict[str, Character] = {}
        cdir = root / "characters"
        if cdir.exists():
            for d in sorted(p for p in cdir.iterdir() if p.is_dir()):
                ap = d / "appearance.yaml"
                if not ap.exists():
                    continue
                with open(ap, encoding="utf-8") as f:
                    app = Appearance.model_validate(yaml.safe_load(f) or {})
                voice = {}
                if (d / "voice.yaml").exists():
                    with open(d / "voice.yaml", encoding="utf-8") as f:
                        voice = yaml.safe_load(f) or {}
                sheet = (d / "sheet.md").read_text(encoding="utf-8") if (d / "sheet.md").exists() else ""
                characters[app.id] = Character(id=app.id, dir=d, appearance=app, sheet=sheet, voice=voice)
        return cls(root=root, premise=read("premise.md"), style=read("style.md"), world=world,
                   locations=parse_locations(world), vocab=Vocab.load(root / "vocab.yaml"),
                   characters=characters, outline=outline, ledger=ledger)

    def scene_path(self, scene_id: str) -> Path:
        return self.root / "scenes" / f"{scene_id}.yaml"

    def scene_ids_written(self) -> list[str]:
        d = self.root / "scenes"
        return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []

    def load_scene(self, scene_id: str) -> Scene:
        return Scene.load(self.scene_path(scene_id))

    def load_scenes(self) -> dict[str, Scene]:
        return {sid: self.load_scene(sid) for sid in self.scene_ids_written()}

    def nsfw_fill(self, ref: str) -> Optional[list[Step]]:
        """Steps from the `## Fill` section of nsfw/<ref>.md, or None if not filled."""
        p = self.root / "nsfw" / f"{ref}.md"
        if not p.exists():
            return None
        text = p.read_text(encoding="utf-8")
        m = re.search(r"^## Fill\s*$(.*)", text, re.M | re.S)
        if not m:
            return None
        body = m.group(1)
        code = re.search(r"```(?:yaml)?\s*\n(.*?)```", body, re.S)
        src = code.group(1) if code else body
        data = yaml.safe_load(src)
        if not data:
            return None
        if isinstance(data, dict) and "steps" in data:
            data = data["steps"]
        return parse_steps(data, f"nsfw/{ref}.md fill")

    # ---- derived ----------------------------------------------------------

    def rating(self) -> str:
        m = re.search(r"^## Content rating\s*\n+\s*`?(\w+)`?", self.premise, re.M)
        return m.group(1) if m else "sfw"

    def ledger_for(self, scene_id: str) -> list[dict[str, Any]]:
        """Ledger entries along the default path up to (not including) scene_id."""
        by_id = self.outline.by_id()
        order: list[str] = []
        seen = set()
        cur: Optional[str] = self.outline.start
        while cur and cur not in seen and cur != scene_id and cur in by_id:
            seen.add(cur)
            order.append(cur)
            cur = by_id[cur].next
        entries = {e["scene"]: e for e in self.ledger if isinstance(e, dict) and "scene" in e}
        return [entries[s] for s in order if s in entries]

    def affection_state(self, scene_id: str) -> dict[str, int]:
        state = {cid: 0 for cid in self.characters}
        for e in self.ledger_for(scene_id):
            for cid, d in (e.get("affection_delta") or {}).items():
                state[cid] = state.get(cid, 0) + int(str(d).replace("+", ""))
        return state

    # ---- context pack -------------------------------------------------------

    def context_pack(self, scene_id: str) -> str:
        """Everything the writer needs for one scene and nothing else (DESIGN.md §1.4)."""
        by_id = self.outline.by_id()
        if scene_id not in by_id:
            raise KeyError(f"{scene_id} is not in outline.yaml")
        o = by_id[scene_id]
        parts: list[str] = []
        parts.append(f"# Context pack — {scene_id}: {o.title}\n")
        parts.append("## Premise\n" + self.premise.strip() + "\n")
        parts.append("## Style\n" + self.style.strip() + "\n")
        loc = self.locations.get(o.location)
        if loc:
            parts.append(f"## Location: {loc.id}\n{loc.description}\n")
        parts.append("## Characters present\n")
        for cid in o.present:
            c = self.characters.get(cid)
            if not c:
                parts.append(f"### {cid}\n(no character directory)\n")
                continue
            a = c.appearance
            outfits = ", ".join(f"{k} ({v.description})" if v.description else k for k, v in a.outfits.items())
            parts.append(f"### {a.display_name} (`{cid}`)\n{c.sheet.strip()}\n\n"
                         f"Outfits: {outfits or '(none)'}; default `{a.default_outfit}`.\n"
                         + (f"Marks: " + "; ".join(f"{m.what} on {m.where}" for m in a.marks) + "\n" if a.marks else ""))
        parts.append("## Route arc\n" + self._arc_section(o.route) + "\n")
        parts.append("## This scene (outline entry)\n```yaml\n"
                     + yaml.safe_dump(o.model_dump(exclude_none=True), sort_keys=False, allow_unicode=True) + "```\n")
        state = self.affection_state(scene_id)
        parts.append("## Affection going in\n" + ", ".join(f"{k}: {v}" for k, v in state.items()) + "\n")
        prev = self.ledger_for(scene_id)
        if prev:
            parts.append("## Continuity ledger (previous scenes, oldest first)\n```yaml\n"
                         + yaml.safe_dump(prev[-6:], sort_keys=False, allow_unicode=True) + "```\n")
            last = prev[-1]
            parts.append("## Previous scene summary\n" + str(last.get("summary", "")) + "\n")
        else:
            parts.append("## Continuity ledger\n(first scene; nothing yet)\n")
        v = self.vocab
        parts.append("## Vocabulary (use only these ids)\n"
                     f"- expressions: {', '.join(v.expressions)}\n"
                     f"- poses: {', '.join(v.poses)}\n"
                     f"- cg_poses: {', '.join(v.cg_poses)}\n"
                     f"- shots: {', '.join(v.shots)}\n"
                     f"- lighting: {', '.join(v.lighting)}\n"
                     f"- deliveries: {', '.join(v.deliveries)}\n"
                     f"- times: {', '.join(v.times)}\n"
                     f"- flags declared: {', '.join(self.outline.flags) or '(none)'}\n")
        return "\n".join(parts)

    def _arc_section(self, route: str) -> str:
        arcs = (self.root / "arcs.md").read_text(encoding="utf-8") if (self.root / "arcs.md").exists() else ""
        if not arcs:
            return "(no arcs.md yet)"
        heading = "Overall" if route == "common" else route
        m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", arcs, re.M | re.S)
        return m.group(1).strip() if m else f"(no `## {heading}` section in arcs.md)"

    # ---- exports for the job runner ----------------------------------------

    def lines(self, scene: Scene) -> list[dict[str, Any]]:
        """Every voiced line in the scene, numbered the way the compiler numbers them."""
        out = []
        n = 0
        for s in self._expanded(scene.steps):
            if isinstance(s, Say) and s.speaker not in SPECIAL_SPEAKERS:
                n += 1
                out.append({"scene": scene.id, "n": n, "speaker": s.speaker, "text": s.text,
                            "delivery": s.delivery or "neutral",
                            "file": f"audio/voice/{scene.id}/{n:03d}.ogg"})
        return out

    def cgs(self, scene: Scene) -> list[dict[str, Any]]:
        out = []
        location, time = scene.location, scene.time
        outfits = {cid: self.characters[cid].appearance.default_outfit for cid in self.characters}
        outfits.update(scene.outfits)
        for s in self._expanded(scene.steps):
            if isinstance(s, Stage):
                location, time = s.location or location, s.time or time
                outfits.update(s.outfits or {})
            elif isinstance(s, CG):
                chars = [{**c.__dict__, "outfit": c.outfit or outfits.get(c.id)} for c in s.characters]
                out.append({"scene": scene.id, "id": s.id, "shot": s.shot, "lighting": s.lighting,
                            "location": location, "time": time, "rating": scene.rating,
                            "characters": chars, "note": s.note, "file": f"images/cg/{s.id}.png"})
        return out

    def _expanded(self, steps: list[Step]):
        """walk() with nsfw refs replaced by their fills when present."""
        for s in steps:
            if isinstance(s, Nsfw):
                fill = self.nsfw_fill(s.ref)
                if fill:
                    yield from self._expanded(fill)
                continue
            yield s
            if isinstance(s, Choice):
                for o in s.options:
                    yield from self._expanded(o.steps)
            elif isinstance(s, If):
                yield from self._expanded(s.then)
                yield from self._expanded(s.else_)


def parse_locations(world: str) -> dict[str, Location]:
    locs: dict[str, Location] = {}
    m = re.search(r"^## Locations\s*$(.*)", world, re.M | re.S)
    if not m:
        return locs
    for sec in re.finditer(r"^### (\S+)\s*$(.*?)(?=^### |\Z)", m.group(1), re.M | re.S):
        lid, body = sec.group(1), sec.group(2)
        times = re.search(r"^\s*-\s*times:\s*\[(.*?)\]", body, re.M)
        bg = re.search(r"^\s*-\s*bg_prompt:\s*\"?(.*?)\"?\s*$", body, re.M)
        desc = "\n".join(l for l in body.strip().splitlines() if not l.strip().startswith("- ")).strip()
        locs[lid] = Location(id=lid, description=desc,
                             times=[t.strip() for t in times.group(1).split(",") if t.strip()] if times else [],
                             bg_prompt=bg.group(1) if bg else "")
    return locs


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------

def validate(project: Project, scene_ids: Optional[list[str]] = None) -> list[str]:
    """Return a list of problems; empty means valid."""
    errs: list[str] = []
    p = project
    v = p.vocab
    by_id = p.outline.by_id()

    # outline
    ids = [s.id for s in p.outline.scenes]
    for dup in {i for i in ids if ids.count(i) > 1}:
        errs.append(f"outline: duplicate scene id {dup}")
    if p.outline.start not in by_id:
        errs.append(f"outline: start scene {p.outline.start} not in scenes")
    for o in p.outline.scenes:
        if o.location not in p.locations:
            errs.append(f"outline {o.id}: location {o.location} not in world.md")
        if o.time not in v.times:
            errs.append(f"outline {o.id}: time {o.time} not in vocab.times")
        for cid in o.present:
            if cid not in p.characters:
                errs.append(f"outline {o.id}: character {cid} has no characters/{cid}/appearance.yaml")
        if o.next and o.next not in by_id:
            errs.append(f"outline {o.id}: next {o.next} does not exist")
        if o.route != "common" and o.route not in p.characters:
            errs.append(f"outline {o.id}: route {o.route} is not a character")
        if o.rating == "explicit" and p.rating() != "explicit":
            errs.append(f"outline {o.id}: explicit scene in a {p.rating()} project")
        if o.requires:
            errs += _check_cond(p, o.requires, f"outline {o.id}.requires")
        if p.outline.branching == "linear" and o.requires:
            errs.append(f"outline {o.id}: 'requires' is not allowed in a linear project")

    # scenes
    try:
        scenes = {sid: p.load_scene(sid) for sid in (scene_ids or p.scene_ids_written())}
    except (SceneError, yaml.YAMLError) as e:
        return errs + [f"scene parse error: {e}"]
    cg_ids: dict[str, str] = {}
    for sid, sc in scenes.items():
        where = f"scene {sid}"
        if sc.id != sid:
            errs.append(f"{where}: file name and id differ ({sc.id})")
        if sid not in by_id:
            errs.append(f"{where}: not in outline.yaml")
        if sc.location not in p.locations:
            errs.append(f"{where}: location {sc.location} not in world.md")
        if sc.time not in v.times:
            errs.append(f"{where}: time {sc.time} not in vocab.times")
        for cid in sc.present:
            if cid not in p.characters:
                errs.append(f"{where}: present character {cid} unknown")
        for cid, outfit in sc.outfits.items():
            errs += _check_outfit(p, cid, outfit, f"{where}.outfits")
        present = set(sc.present)
        open_cg = False
        for i, s in enumerate(_flat_with_nsfw(p, sc.steps, errs, where)):
            w = f"{where} step {i} ({type(s).__name__})"
            if isinstance(s, Say):
                if s.speaker not in SPECIAL_SPEAKERS:
                    if s.speaker not in p.characters:
                        errs.append(f"{w}: unknown speaker {s.speaker}")
                    elif s.speaker not in present:
                        errs.append(f"{w}: {s.speaker} speaks but is not present")
                if s.expr and s.expr not in v.expressions:
                    errs.append(f"{w}: expression {s.expr} not in vocab")
                if s.pose and s.pose not in v.poses:
                    errs.append(f"{w}: pose {s.pose} not in vocab")
                if s.delivery and s.delivery not in v.deliveries:
                    errs.append(f"{w}: delivery {s.delivery} not in vocab")
                if not s.text.strip():
                    errs.append(f"{w}: empty line")
            elif isinstance(s, Stage):
                if s.location and s.location not in p.locations:
                    errs.append(f"{w}: location {s.location} not in world.md")
                if s.time and s.time not in v.times:
                    errs.append(f"{w}: time {s.time} not in vocab")
                if s.transition and s.transition not in v.transitions:
                    errs.append(f"{w}: transition {s.transition} not in vocab")
                if s.present is not None:
                    for cid in s.present:
                        if cid not in p.characters:
                            errs.append(f"{w}: character {cid} unknown")
                    present = set(s.present)
                for cid, outfit in (s.outfits or {}).items():
                    errs += _check_outfit(p, cid, outfit, w)
            elif isinstance(s, Enter):
                if s.char not in p.characters:
                    errs.append(f"{w}: character {s.char} unknown")
                if s.pose and s.pose not in v.poses:
                    errs.append(f"{w}: pose {s.pose} not in vocab")
                if s.expr and s.expr not in v.expressions:
                    errs.append(f"{w}: expression {s.expr} not in vocab")
                if s.at and s.at not in v.positions:
                    errs.append(f"{w}: position {s.at} not in vocab")
                present.add(s.char)
            elif isinstance(s, Exit):
                if s.char not in present:
                    errs.append(f"{w}: {s.char} exits but is not present")
                present.discard(s.char)
            elif isinstance(s, SetFlags):
                for k in s.values:
                    if k not in p.outline.flags:
                        errs.append(f"{w}: flag {k} is not declared in outline.flags")
            elif isinstance(s, Affection):
                for cid in s.deltas:
                    if cid not in p.characters:
                        errs.append(f"{w}: affection for unknown character {cid}")
            elif isinstance(s, CG):
                if open_cg:
                    errs.append(f"{w}: cg {s.id} opened while another cg is still showing")
                open_cg = True
                if s.id in cg_ids and cg_ids[s.id] != sid:
                    errs.append(f"{w}: cg id {s.id} already used in {cg_ids[s.id]}")
                cg_ids[s.id] = sid
                if s.shot not in v.shots:
                    errs.append(f"{w}: shot {s.shot} not in vocab")
                if s.lighting not in v.lighting:
                    errs.append(f"{w}: lighting {s.lighting} not in vocab")
                if s.transition and s.transition not in v.transitions:
                    errs.append(f"{w}: transition {s.transition} not in vocab")
                for c in s.characters:
                    if c.id not in p.characters:
                        errs.append(f"{w}: cg character {c.id} unknown")
                    if c.pose not in v.cg_poses:
                        errs.append(f"{w}: cg pose {c.pose} not in vocab.cg_poses")
                    if c.expr not in v.expressions:
                        errs.append(f"{w}: cg expression {c.expr} not in vocab")
                    if c.outfit:
                        errs += _check_outfit(p, c.id, c.outfit, w)
            elif isinstance(s, CGEnd):
                if not open_cg:
                    errs.append(f"{w}: cg_end without an open cg")
                open_cg = False
            elif isinstance(s, Choice):
                for j, o in enumerate(s.options):
                    if o.goto and o.goto not in by_id:
                        errs.append(f"{w}: option {j} goto {o.goto} does not exist")
                    if o.requires:
                        errs += _check_cond(p, o.requires, f"{w} option {j}.requires")
                    for cid in (o.effects.get("affection") or {}):
                        if cid not in p.characters:
                            errs.append(f"{w}: option {j} affection for unknown character {cid}")
                    for k in (o.effects.get("set") or {}):
                        if k not in p.outline.flags:
                            errs.append(f"{w}: option {j} sets undeclared flag {k}")
                    if p.outline.branching == "linear" and (o.goto or o.effects.get("set")):
                        errs.append(f"{w}: option {j} branches, but the project is linear")
            elif isinstance(s, If):
                errs += _check_cond(p, s.cond, w)
            elif isinstance(s, Jump):
                if s.target not in by_id:
                    errs.append(f"{w}: jump target {s.target} does not exist")
        if open_cg:
            errs.append(f"{where}: scene ends with a cg still showing (missing cg_end)")
        if sc.next and sc.next not in by_id:
            errs.append(f"{where}: next {sc.next} does not exist")
        if sc.rating == "explicit" and p.rating() != "explicit":
            errs.append(f"{where}: explicit scene in a {p.rating()} project")

    # reachability over the outline + written scenes
    reach = _reachable(p, scenes)
    for o in p.outline.scenes:
        if o.id not in reach:
            errs.append(f"outline {o.id}: unreachable from {p.outline.start}")
    return errs



def _flat_with_nsfw(p: Project, steps: list[Step], errs: list[str], where: str):
    """Depth-first steps with nsfw refs expanded."""
    for s in walk(steps):
        if isinstance(s, Nsfw):
            fill = p.nsfw_fill(s.ref)
            if fill is None:
                if not (p.root / "nsfw" / f"{s.ref}.md").exists():
                    errs.append(f"{where}: nsfw ref {s.ref} has no nsfw/{s.ref}.md")
                continue
            yield from walk(fill)
            continue
        yield s



def _check_outfit(p: Project, cid: str, outfit: str, where: str) -> list[str]:
    c = p.characters.get(cid)
    if not c:
        return [f"{where}: outfit for unknown character {cid}"]
    if outfit not in c.appearance.outfits:
        return [f"{where}: {cid} has no outfit {outfit}"]
    return []


def _check_cond(p: Project, cond: str, where: str) -> list[str]:
    errs = []
    try:
        compile_condition(cond)
    except SceneError as e:
        return [f"{where}: {e}"]
    for kind, name in condition_refs(cond):
        if kind == "affection" and name not in p.characters:
            errs.append(f"{where}: affection.{name} is not a character")
        if kind == "flags" and name not in p.outline.flags:
            errs.append(f"{where}: flags.{name} is not declared in outline.flags")
    return errs


def _reachable(p: Project, scenes: dict[str, Scene]) -> set[str]:
    by_id = p.outline.by_id()
    seen: set[str] = set()
    stack = [p.outline.start]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in by_id:
            continue
        seen.add(cur)
        o = by_id[cur]
        if o.next:
            stack.append(o.next)
        sc = scenes.get(cur)
        if sc:
            if sc.next:
                stack.append(sc.next)
            for s in walk(sc.steps):
                if isinstance(s, Jump):
                    stack.append(s.target)
                elif isinstance(s, Choice):
                    stack += [o.goto for o in s.options if o.goto]
    return seen
