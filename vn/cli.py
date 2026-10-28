"""`python -m vn <command> [project] ...`"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .project import Project, validate

HERE = Path(__file__).resolve().parent.parent


def cmd_new(a):
    dst = Path(a.project)
    if dst.exists() and any(dst.iterdir()):
        sys.exit(f"{dst} exists and is not empty")
    shutil.copytree(HERE / "templates" / "project", dst, dirs_exist_ok=True)
    print(f"created {dst}")


def cmd_new_character(a):
    p = Project.load(a.project)
    d = p.root / "characters" / a.id
    if d.exists():
        sys.exit(f"{d} exists")
    shutil.copytree(HERE / "templates" / "character", d)
    for f in d.iterdir():
        f.write_text(f.read_text(encoding="utf-8").replace("<id>", a.id), encoding="utf-8")
    (d / "art" / "sprites").mkdir(parents=True)
    (d / "art" / "canon").mkdir(parents=True)
    print(f"created {d}")


def cmd_validate(a):
    p = Project.load(a.project)
    errs = validate(p, a.scene or None)
    for e in errs:
        print("ERROR", e)
    print(f"{len(errs)} problem(s)" if errs else "valid")
    sys.exit(1 if errs else 0)


def cmd_pack(a):
    p = Project.load(a.project)
    print(p.context_pack(a.scene))


def cmd_status(a):
    p = Project.load(a.project)
    written = set(p.scene_ids_written())
    ledgered = {e.get("scene") for e in p.ledger if isinstance(e, dict)}
    print(f"project: {p.root}  rating: {p.rating()}  branching: {p.outline.branching}")
    print(f"characters: {', '.join(p.characters) or '(none)'}")
    print(f"locations: {', '.join(p.locations) or '(none)'}")
    print(f"scenes in outline: {len(p.outline.scenes)}  written: {len(written)}  ledgered: {len(ledgered & written)}")
    for o in p.outline.scenes:
        mark = "L" if o.id in ledgered else ("w" if o.id in written else ".")
        print(f"  [{mark}] {o.id:14} {o.route:10} {o.rating:10} {o.title}")
    print("next to write:", next((o.id for o in p.outline.scenes if o.id not in written), "(all written)"))


def cmd_lines(a):
    p = Project.load(a.project)
    ids = a.scene or p.scene_ids_written()
    out = [l for sid in ids for l in p.lines(p.load_scene(sid))]
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_cgs(a):
    p = Project.load(a.project)
    ids = a.scene or p.scene_ids_written()
    out = [c for sid in ids for c in p.cgs(p.load_scene(sid))]
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_build(a):
    from .compile_renpy import build
    p = Project.load(a.project)
    errs = validate(p)
    if errs:
        for e in errs:
            print("ERROR", e)
        sys.exit("fix validation errors before building")
    out = Path(a.out) if a.out else p.root / "build"
    written = build(p, out, None, Path(a.template) if a.template else None)
    print(f"built {len(written)} scene(s) into {out / 'game'}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vn")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new", help="create a project from the template"); s.add_argument("project"); s.set_defaults(f=cmd_new)
    s = sub.add_parser("new-character"); s.add_argument("project"); s.add_argument("id"); s.set_defaults(f=cmd_new_character)
    s = sub.add_parser("validate"); s.add_argument("project"); s.add_argument("scene", nargs="*"); s.set_defaults(f=cmd_validate)
    s = sub.add_parser("pack", help="print the writer's context pack for a scene"); s.add_argument("project"); s.add_argument("scene"); s.set_defaults(f=cmd_pack)
    s = sub.add_parser("status"); s.add_argument("project"); s.set_defaults(f=cmd_status)
    s = sub.add_parser("lines", help="voiced lines as JSON (TTS job list)"); s.add_argument("project"); s.add_argument("scene", nargs="*"); s.set_defaults(f=cmd_lines)
    s = sub.add_parser("cgs", help="CG shot specs as JSON (image job list)"); s.add_argument("project"); s.add_argument("scene", nargs="*"); s.set_defaults(f=cmd_cgs)
    s = sub.add_parser("build", help="compile to a Ren'Py game/ directory"); s.add_argument("project"); s.add_argument("--out"); s.add_argument("--template", help="a launcher-made Ren'Py project to take gui/screens from"); s.set_defaults(f=cmd_build)
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
