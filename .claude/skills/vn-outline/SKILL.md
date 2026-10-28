---
name: vn-outline
description: Break the approved arcs into a scene-by-scene outline (outline.yaml) for a vn project, with locations, participants, ratings, goals, beats and the branch structure. Run after /vn-arcs.
---

# /vn-outline <project>

Read `DESIGN.md` §3.8 and §8, `arcs.md`, `world.md`, the sheets, `premise.md`.

1. Decide the scene list. Ids: `c<act>_s<nn>` for common, `c<act>_<char>_s<nn>`
   for routes. Each entry needs `location` (a `world.md` id), `time`,
   `present`, `rating`, a one-sentence `goal`, 3–5 `beats`, and `next`.
   Scenes with CG moments list them under `cg` as intent only.
2. Branch structure by level:
   - `linear`: a single `next` chain. No `requires`.
   - `routes`: common chain → one route-lock scene whose choice `goto`s each
     route's first scene (gated by `affection.<id> >= N` from `arcs.md`) →
     per-route chains. Declare any flags in `flags:`.
   - `branching`: `requires`/`goto` anywhere, but every divergence rejoins
     within 3 scenes or ends. Report the path count before writing.
3. Explicit scenes: only in an `explicit` project; mark `rating: explicit`
   and, if the human has direction for what happens, put it in `direction:`.
4. Pacing check: no three consecutive scenes in the same location; every
   love interest appears in the common route at least twice before the lock;
   every route has a scene that only that route can have.
5. `python -m vn validate projects/<slug>` must be clean (the outline is
   validated even with no scenes written). `python -m vn status` shows the
   list.
6. **Checkpoint**: approval of the outline. Next: `/vn-write-scene` for the
   first scene, or `/vn-write-all`.
