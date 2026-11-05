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
            if s.outfits:
                bits.append("outfits=" + ",".join(f"{k}:{v}" for k, v in s.outfits.items()))
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


def render_items(p: Project, sc: Scene) -> list[dict]:
    """The scene as flat display items for the console: one dict per step, with depth."""
    items: list[dict] = []
    n = [0]

    def walk_(steps, depth):
        for s in steps:
            d = {"depth": depth, "kind": type(s).__name__.lower()}
            if isinstance(s, Narr):
                d.update(text=s.text)
            elif isinstance(s, Say):
                if s.speaker not in ("player", "narrator"):
                    n[0] += 1; d["line"] = n[0]
                d.update(speaker=s.speaker, text=s.text, expr=s.expr, pose=s.pose, delivery=s.delivery)
            elif isinstance(s, Stage):
                d.update(location=s.location, time=s.time, present=s.present, outfits=s.outfits)
            elif isinstance(s, (Enter, Exit)):
                d.update(char=s.char)
            elif isinstance(s, CG):
                d.update(id=s.id, shot=s.shot, lighting=s.lighting, note=s.note,
                         characters=[c.__dict__ for c in s.characters])
            elif isinstance(s, Choice):
                d.update(prompt=s.prompt)
                items.append(d)
                for o in s.options:
                    items.append({"depth": depth + 1, "kind": "option", "text": o.text, "effects": o.effects,
                                  "goto": o.goto, "requires": o.requires})
                    walk_(o.steps, depth + 2)
                continue
            elif isinstance(s, If):
                d.update(cond=s.cond)
                items.append(d)
                walk_(s.then, depth + 1)
                if s.else_:
                    items.append({"depth": depth, "kind": "else"})
                    walk_(s.else_, depth + 1)
                continue
            elif isinstance(s, SetFlags):
                d.update(values=s.values)
            elif isinstance(s, Affection):
                d.update(deltas=s.deltas)
            elif isinstance(s, Nsfw):
                d.update(ref=s.ref)
            elif isinstance(s, (Jump, Call)):
                d.update(target=s.target)
            items.append(d)

    walk_(p.expand(sc.steps), 0)
    for i, d in enumerate(items):
        d["i"] = i
    return items
