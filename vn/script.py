"""Render a scene as a readable markdown script, for human review."""
from __future__ import annotations

from .project import Project
from .schema import (CG, Affection, Call, CGEnd, Choice, Enter, Exit, If, Jump, Narr, Nsfw, Pause, Say, Scene,
                     SetFlags, Stage, Step)


def render(p: Project, sc: Scene) -> str:
    out = [f"## {sc.id} — {sc.title}", "",
           f"*{sc.location}, {sc.time}. Present: {', '.join(sc.present) or 'nobody'}. Intimacy {sc.intimacy}, {sc.rating}.*", ""]
    _steps(p, p.expand(sc.steps), out, 0)
    out.append(f"*→ {sc.next}*" if sc.next else "*→ returns / ends*")
    return "\n".join(out)


def _name(p: Project, cid: str) -> str:
    if cid == "player":
        return "**You**"
    c = p.characters.get(cid)
    return f"**{c.appearance.display_name}**" if c else f"**{cid}**"


def _steps(p: Project, steps: list[Step], out: list[str], depth: int) -> None:
    ind = "> " * depth
    for s in steps:
        if isinstance(s, Narr):
            out.append(f"{ind}*{s.text}*"); out.append(ind.rstrip() if ind else "")
        elif isinstance(s, Say):
            tags = ", ".join(x for x in (s.expr, s.pose, s.delivery) if x)
            out.append(f"{ind}{_name(p, s.speaker)} ({tags}): {s.text}" if tags else f"{ind}{_name(p, s.speaker)}: {s.text}")
            out.append(ind.rstrip() if ind else "")
        elif isinstance(s, Stage):
            bits = [f"{k}={v}" for k, v in (("location", s.location), ("time", s.time)) if v]
            if s.present is not None:
                bits.append("present=" + ",".join(s.present))
            out.append(f"{ind}`[stage: {' '.join(bits)}]`"); out.append("")
        elif isinstance(s, Enter):
            out.append(f"{ind}`[enter {s.char}]`"); out.append("")
        elif isinstance(s, Exit):
            out.append(f"{ind}`[exit {s.char}]`"); out.append("")
        elif isinstance(s, CG):
            who = "; ".join(f"{c.id} {c.pose}/{c.expr}" + (f"/{c.outfit}" if c.outfit else "") for c in s.characters) or "no one"
            out.append(f"{ind}`[CG {s.id}: {s.shot}, {s.lighting}; {who}]` {s.note}"); out.append("")
        elif isinstance(s, CGEnd):
            out.append(f"{ind}`[CG ends]`"); out.append("")
        elif isinstance(s, Choice):
            out.append(f"{ind}**Choice** — {s.prompt}"); out.append("")
            for o in s.options:
                eff = []
                for cid, d in (o.effects.get("affection") or {}).items():
                    eff.append(f"{cid} {d:+d}")
                for k, v in (o.effects.get("set") or {}).items():
                    eff.append(f"{k}={v}")
                extra = (f" *({', '.join(eff)})*" if eff else "") + (f" *[requires {o.requires}]*" if o.requires else "") + (f" → {o.goto}" if o.goto else "")
                out.append(f"{ind}- **{o.text}**{extra}"); out.append("")
                _steps(p, o.steps, out, depth + 1)
        elif isinstance(s, If):
            out.append(f"{ind}`[if {s.cond}]`"); out.append("")
            _steps(p, s.then, out, depth + 1)
            if s.else_:
                out.append(f"{ind}`[else]`"); out.append("")
                _steps(p, s.else_, out, depth + 1)
        elif isinstance(s, SetFlags):
            out.append(f"{ind}`[set {', '.join(f'{k}={v}' for k, v in s.values.items())}]`"); out.append("")
        elif isinstance(s, Affection):
            out.append(f"{ind}`[affection {', '.join(f'{k} {v:+d}' for k, v in s.deltas.items())}]`"); out.append("")
        elif isinstance(s, Nsfw):
            out.append(f"{ind}`[explicit section {s.ref}: not filled]`"); out.append("")
        elif isinstance(s, Jump):
            out.append(f"{ind}`[jump {s.target}]`"); out.append("")
        elif isinstance(s, Call):
            out.append(f"{ind}`[call {s.target}]`"); out.append("")
        elif isinstance(s, Pause):
            out.append(f"{ind}`[pause]`"); out.append("")
