"""Scene / outline / character models and the validator (DESIGN.md §3–§6).

Steps are parsed by hand rather than as a pydantic discriminated union so the
YAML stays terse (`- narr: "..."`, `- say: mira`), and so error messages can
name the step index and the offending key. A step's kind is whichever of its
keys is in STEP_KINDS; key order never matters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------
# bible files
# --------------------------------------------------------------------------

class Outfit(BaseModel):
    tags: str = ""
    description: str = ""


class Mark(BaseModel):
    what: str
    where: str
    tags: str = ""


class LoraRef(BaseModel):
    file: str
    trigger: str = ""
    weight: float = 0.8


class Appearance(BaseModel):
    id: str
    display_name: str
    age: int
    base_tags: str = ""
    marks: list[Mark] = Field(default_factory=list)
    outfits: dict[str, Outfit] = Field(default_factory=dict)
    default_outfit: str = "casual"
    style_tags: str = ""
    negative_tags: str = ""
    lora: Optional[LoraRef] = None
    color: Optional[str] = None          # name colour in the text box, "#rrggbb"

    @field_validator("age")
    @classmethod
    def _adult(cls, v: int) -> int:
        if v < 18:
            raise ValueError("every character must be an adult (age >= 18)")
        return v


class OutlineScene(BaseModel):
    id: str
    title: str = ""
    act: int = 1
    route: str = "common"
    location: str
    time: str = "afternoon"
    present: list[str] = Field(default_factory=list)
    rating: str = "sfw"
    goal: str = ""
    beats: list[str] = Field(default_factory=list)
    requires: Optional[str] = None
    next: Optional[str] = None
    cg: list[str] = Field(default_factory=list)
    direction: Optional[str] = None      # human's direction for explicit sections

    @field_validator("rating")
    @classmethod
    def _rating(cls, v: str) -> str:
        if v not in ("sfw", "suggestive", "explicit"):
            raise ValueError("rating must be sfw | suggestive | explicit")
        return v


class Outline(BaseModel):
    branching: str = "routes"
    start: str
    flags: dict[str, Any] = Field(default_factory=dict)
    scenes: list[OutlineScene] = Field(default_factory=list)

    @field_validator("branching")
    @classmethod
    def _branching(cls, v: str) -> str:
        if v not in ("linear", "routes", "branching"):
            raise ValueError("branching must be linear | routes | branching")
        return v

    def by_id(self) -> dict[str, OutlineScene]:
        return {s.id: s for s in self.scenes}


# --------------------------------------------------------------------------
# scene steps
# --------------------------------------------------------------------------

@dataclass
class Narr:
    text: str


@dataclass
class Say:
    speaker: str
    text: str
    expr: Optional[str] = None
    pose: Optional[str] = None
    delivery: Optional[str] = None


@dataclass
class Stage:
    location: Optional[str] = None
    time: Optional[str] = None
    present: Optional[list[str]] = None
    outfits: Optional[dict[str, str]] = None
    transition: Optional[str] = None


@dataclass
class Enter:
    char: str
    pose: Optional[str] = None
    expr: Optional[str] = None
    at: Optional[str] = None
    transition: Optional[str] = None


@dataclass
class Exit:
    char: str
    transition: Optional[str] = None


@dataclass
class SetFlags:
    values: dict[str, Any]


@dataclass
class Affection:
    deltas: dict[str, int]


@dataclass
class CGChar:
    id: str
    pose: str
    expr: str
    outfit: Optional[str] = None
    gaze: str = "at_viewer"


@dataclass
class CG:
    id: str
    shot: str
    lighting: str
    characters: list[CGChar] = field(default_factory=list)
    note: str = ""
    transition: Optional[str] = None


@dataclass
class CGEnd:
    transition: Optional[str] = None


@dataclass
class Option:
    text: str
    effects: dict[str, Any] = field(default_factory=dict)   # {affection: {...}, set: {...}}
    goto: Optional[str] = None
    requires: Optional[str] = None
    steps: list = field(default_factory=list)


@dataclass
class Choice:
    prompt: str
    options: list[Option]


@dataclass
class If:
    cond: str
    then: list
    else_: list = field(default_factory=list)


@dataclass
class Nsfw:
    ref: str


@dataclass
class Jump:
    target: str


@dataclass
class Pause:
    seconds: float = 0.5


Step = Union[Narr, Say, Stage, Enter, Exit, SetFlags, Affection, CG, CGEnd, Choice, If, Nsfw, Jump, Pause]


class SceneError(ValueError):
    pass


STEP_KINDS = {"narr", "say", "stage", "enter", "exit", "set", "affection", "cg", "cg_end",
              "choice", "if", "nsfw", "jump", "pause"}


def _delta(v: Any) -> int:
    if isinstance(v, bool):
        raise SceneError(f"affection delta must be a number, got {v!r}")
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().replace(" ", "")
        if re.fullmatch(r"[+-]?\d+", s):
            return int(s)
    raise SceneError(f"affection delta must be like +1 / -2, got {v!r}")


def parse_steps(raw: Any, where: str = "steps") -> list[Step]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SceneError(f"{where}: expected a list of steps")
    out: list[Step] = []
    for i, d in enumerate(raw):
        out.append(parse_step(d, f"{where}[{i}]"))
    return out


def parse_step(d: Any, where: str) -> Step:
    if not isinstance(d, dict) or not d:
        raise SceneError(f"{where}: a step must be a mapping with one leading key")
    kinds = [k for k in d if k in STEP_KINDS]
    if len(kinds) != 1:
        raise SceneError(f"{where}: a step must have exactly one of {sorted(STEP_KINDS)}, got {sorted(d)}")
    kind = kinds[0]
    rest = {k: v for k, v in d.items() if k != kind}
    v = d[kind]

    def only(allowed: set[str]) -> None:
        extra = set(rest) - allowed
        if extra:
            raise SceneError(f"{where}: unknown keys for '{kind}': {sorted(extra)}")

    if kind == "narr":
        only(set())
        return Narr(text=str(v))
    if kind == "say":
        only({"text", "expr", "pose", "delivery"})
        if "text" not in rest:
            raise SceneError(f"{where}: 'say' needs 'text'")
        return Say(speaker=str(v), text=str(rest["text"]), expr=rest.get("expr"),
                   pose=rest.get("pose"), delivery=rest.get("delivery"))
    if kind == "stage":
        only(set())
        v = v or {}
        extra = set(v) - {"location", "time", "present", "outfits", "transition"}
        if extra:
            raise SceneError(f"{where}: unknown stage keys {sorted(extra)}")
        return Stage(**v)
    if kind == "enter":
        only(set())
        if isinstance(v, str):
            return Enter(char=v)
        return Enter(**v)
    if kind == "exit":
        only(set())
        if isinstance(v, str):
            return Exit(char=v)
        return Exit(**v)
    if kind == "set":
        only(set())
        if not isinstance(v, dict) or not v:
            raise SceneError(f"{where}: 'set' needs a mapping of flag: value")
        return SetFlags(values=dict(v))
    if kind == "affection":
        only(set())
        if not isinstance(v, dict) or not v:
            raise SceneError(f"{where}: 'affection' needs a mapping of character: delta")
        return Affection(deltas={k: _delta(x) for k, x in v.items()})
    if kind == "cg":
        only(set())
        if not isinstance(v, dict):
            raise SceneError(f"{where}: 'cg' needs a mapping")
        for k in ("id", "shot", "lighting"):
            if k not in v:
                raise SceneError(f"{where}: cg needs '{k}'")
        chars = []
        for j, c in enumerate(v.get("characters") or []):
            for k in ("id", "pose", "expr"):
                if k not in c:
                    raise SceneError(f"{where}: cg.characters[{j}] needs '{k}'")
            chars.append(CGChar(**c))
        return CG(id=v["id"], shot=v["shot"], lighting=v["lighting"], characters=chars,
                  note=v.get("note", ""), transition=v.get("transition"))
    if kind == "cg_end":
        only(set())
        v = v or {}
        return CGEnd(transition=v.get("transition") if isinstance(v, dict) else None)
    if kind == "choice":
        only(set())
        if not isinstance(v, dict) or "options" not in v:
            raise SceneError(f"{where}: 'choice' needs 'options'")
        opts = []
        for j, o in enumerate(v["options"]):
            if "text" not in o:
                raise SceneError(f"{where}: option[{j}] needs 'text'")
            extra = set(o) - {"text", "effects", "goto", "requires", "steps"}
            if extra:
                raise SceneError(f"{where}: option[{j}] unknown keys {sorted(extra)}")
            eff = o.get("effects") or {}
            bad = set(eff) - {"affection", "set"}
            if bad:
                raise SceneError(f"{where}: option[{j}].effects unknown keys {sorted(bad)}")
            if "affection" in eff:
                eff = {**eff, "affection": {k: _delta(x) for k, x in eff["affection"].items()}}
            opts.append(Option(text=str(o["text"]), effects=eff, goto=o.get("goto"),
                               requires=o.get("requires"),
                               steps=parse_steps(o.get("steps"), f"{where}.options[{j}].steps")))
        if len(opts) < 2:
            raise SceneError(f"{where}: a choice needs at least two options")
        return Choice(prompt=str(v.get("prompt", "")), options=opts)
    if kind == "if":
        only({"then", "else"})
        return If(cond=str(v), then=parse_steps(rest.get("then"), f"{where}.then"),
                  else_=parse_steps(rest.get("else"), f"{where}.else"))
    if kind == "nsfw":
        only(set())
        if isinstance(v, str):
            return Nsfw(ref=v)
        if not isinstance(v, dict) or "ref" not in v:
            raise SceneError(f"{where}: 'nsfw' needs 'ref'")
        return Nsfw(ref=v["ref"])
    if kind == "jump":
        only(set())
        return Jump(target=str(v))
    if kind == "pause":
        only(set())
        return Pause(seconds=float(v or 0.5))
    raise SceneError(f"{where}: unknown step kind '{kind}'")


@dataclass
class Scene:
    id: str
    title: str
    location: str
    time: str
    present: list[str]
    outfits: dict[str, str]
    rating: str
    steps: list[Step]
    next: Optional[str]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Scene":
        for k in ("id", "location", "time", "steps"):
            if k not in d:
                raise SceneError(f"scene needs '{k}'")
        extra = set(d) - {"id", "title", "location", "time", "present", "outfits", "rating", "steps", "next"}
        if extra:
            raise SceneError(f"scene {d['id']}: unknown keys {sorted(extra)}")
        return cls(id=d["id"], title=d.get("title", ""), location=d["location"], time=d["time"],
                   present=list(d.get("present") or []), outfits=dict(d.get("outfits") or {}),
                   rating=d.get("rating", "sfw"), steps=parse_steps(d["steps"]), next=d.get("next"))

    @classmethod
    def load(cls, path) -> "Scene":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f) or {})


def walk(steps: list[Step]):
    """Yield every step, depth first, including those nested in choices and ifs."""
    for s in steps:
        yield s
        if isinstance(s, Choice):
            for o in s.options:
                yield from walk(o.steps)
        elif isinstance(s, If):
            yield from walk(s.then)
            yield from walk(s.else_)


# --------------------------------------------------------------------------
# conditions
# --------------------------------------------------------------------------

_COND_REF = re.compile(r"\b(affection|flags)\.([A-Za-z_][A-Za-z0-9_]*)")
_COND_ALLOWED = re.compile(r"^[\sA-Za-z0-9_.<>=!()+\-*/'\"]*$")


def condition_refs(cond: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _COND_REF.finditer(cond)]


def compile_condition(cond: str) -> str:
    """`affection.mira >= 3 and flags.met_mira` -> Python over the two dicts."""
    if not _COND_ALLOWED.match(cond):
        raise SceneError(f"condition has characters outside the allowed set: {cond!r}")
    return _COND_REF.sub(lambda m: f'{m.group(1)}["{m.group(2)}"]', cond)
