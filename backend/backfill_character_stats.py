"""Give a project that predates seeded stats the default relationship stat.

`store.create_project` now declares `DEFAULT_CHARACTER_STATS`, but projects made
before that have `character_stats: []` -- so `context.stat_line` renders nothing
and every character card reports the day-one `relationship_to_player` for the
whole story. This backfills them.

The second half is the part that matters. A project that tracked affection
before stats existed did it with hand-declared variables named
`affection_<character>`, and `graph.merged_flag_defaults` deliberately lets a
declared variable win over the stat schema -- a compatibility guarantee, so an
older project is not silently reset. Left in place that guarantee defeats the
backfill: the stat is declared, and every character who already had a variable
keeps its old default and gets no band. So a declaration is dropped when it is
exactly the variable the new stat now owns, and nothing else is touched.

Dropping it is safe for the story logic. Nodes reference variables by name, and
`collect_flags` picks up any name a Set or Choice writes, so the effects keep
firing; all that changes is who supplies the default, which becomes the stat.

Dry run by default. `--apply` writes.

    python backfill_character_stats.py --project p_006e15...        # preview
    python backfill_character_stats.py --project p_006e15... --apply
"""
from __future__ import annotations

import argparse
import json
import sys

from app import db, store
from app.defaults import DEFAULT_CHARACTER_STATS
from app.models import StatDef


def plan_for(project_id: str) -> dict:
    """What the backfill would change, without changing it."""
    project = store.get_project(project_id)
    if project is None:
        raise SystemExit(f"no project {project_id}")

    have = {s.id for s in project.character_stats}
    add = [StatDef(**s) for s in DEFAULT_CHARACTER_STATS if s["id"] not in have]

    # Only the exact variables the incoming stats now own: `{stat}_{character}`
    # for a character this project actually has. A variable that merely starts
    # with "affection" is someone else's and stays.
    owned = {f"{s.id}_{c.id}"
             for s in add
             for c in store.list_characters(project_id)}
    drop = [v for v in project.variables if v.name in owned]

    return {"project": project, "add": add, "drop": drop}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    db.init_db()
    plan = plan_for(args.project)
    project, add, drop = plan["project"], plan["add"], plan["drop"]

    print(f"{project.name} ({project.id})")
    print(f"  stats to add : {[s.id for s in add] or 'none -- already seeded'}")
    print(f"  vars to drop : {[v.name for v in drop] or 'none'}")
    for v in drop:
        print(f"      {v.name}: type={v.type} default={v.default!r} -> stat default")

    if not add and not drop:
        print("  nothing to do")
        return 0
    if not args.apply:
        print("\n  dry run -- pass --apply to write")
        return 0

    dropped = {v.name for v in drop}
    project.character_stats = list(project.character_stats) + add
    project.variables = [v for v in project.variables if v.name not in dropped]
    store.save_project(project)
    print("\n  written")

    fresh = store.get_project(args.project)
    print("  stats now:", json.dumps([s.id for s in fresh.character_stats]))
    print("  vars now :", json.dumps([v.name for v in fresh.variables]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
