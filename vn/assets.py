"""What each scene needs on disk, and whether it is there (used by the console)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .compile_renpy import Compiler
from .project import Project
from .schema import Scene


def scene_assets(p: Project, sc: Scene) -> dict[str, Any]:
    """Sprites, backgrounds, CGs and voice lines a scene uses, each with `present`."""
    c = Compiler(p)
    c.compile_scene(sc)
    sprites = []
    for name in sorted(c.images):
        if name[0] == "cg":
            continue
        cid, outfit, pose, expr = name
        f = p.root / "characters" / cid / "art" / "sprites" / f"{outfit}_{pose}_{expr}.png"
        sprites.append({"char": cid, "outfit": outfit, "pose": pose, "expr": expr,
                        "file": str(f.relative_to(p.root)), "present": f.exists()})
    bgs = []
    for loc, t in sorted(c.bgs):
        f = p.root / "images" / "bg" / f"{loc}_{t}.png"
        bgs.append({"location": loc, "time": t, "file": str(f.relative_to(p.root)), "present": f.exists()})
    cgs = []
    for cg in p.cgs(sc):
        f = p.root / cg["file"]
        cand = p.root / "review" / "cg" / cg["id"]
        cgs.append({**cg, "present": f.exists(),
                    "candidates": sorted(str(x.relative_to(p.root)) for x in cand.glob("*.png")) if cand.exists() else []})
    lines = []
    for l in p.lines(sc):
        f = p.root / l["file"]
        if not f.exists() and f.with_suffix(".wav").exists():
            f = f.with_suffix(".wav"); l = {**l, "file": str(f.relative_to(p.root))}
        takes_dir = p.root / "audio" / "takes" / sc.id / f"{l['n']:03d}"
        lines.append({**l, "present": f.exists(),
                      "takes": sorted(str(x.relative_to(p.root)) for x in takes_dir.glob("*.*")
                                      if x.suffix in (".ogg", ".wav", ".mp3")) if takes_dir.exists() else []})
    return {"sprites": sprites, "backgrounds": bgs, "cgs": cgs, "lines": lines,
            "missing": {"sprites": sum(not s["present"] for s in sprites),
                        "backgrounds": sum(not b["present"] for b in bgs),
                        "cgs": sum(not g["present"] for g in cgs),
                        "lines": sum(not l["present"] for l in lines)}}


def project_board(p: Project) -> list[dict[str, Any]]:
    """One row per outline scene: written, ledgered, and missing-asset counts."""
    written = set(p.scene_ids_written())
    ledgered = {e.get("scene") for e in p.ledger if isinstance(e, dict)}
    rows = []
    for o in p.outline.scenes:
        row = {"id": o.id, "title": o.title, "act": o.act, "route": o.route, "location": o.location,
               "time": o.time, "rating": o.rating, "intimacy": o.intimacy,
               "written": o.id in written, "ledgered": o.id in ledgered, "missing": None,
               "notes": _note_count(p, o.id)}
        if o.id in written:
            try:
                row["missing"] = scene_assets(p, p.load_scene(o.id))["missing"]
            except Exception as e:  # a half-written scene must not break the board
                row["error"] = str(e)
        rows.append(row)
    return rows


def _note_count(p: Project, scene_id: str) -> int:
    f = p.root / "review" / "notes" / f"{scene_id}.yaml"
    if not f.exists():
        return 0
    import yaml
    with open(f, encoding="utf-8") as fh:
        notes = yaml.safe_load(fh) or []
    return sum(1 for n in notes if isinstance(n, dict) and not n.get("applied"))
