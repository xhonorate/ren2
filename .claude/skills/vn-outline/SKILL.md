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
   - `routes`, exclusive: common chain → one route-lock scene whose choice
     `goto`s each route's first scene (gated by affection from `arcs.md`) →
     per-route chains. Declare any flags in `flags:`.
   - `routes`, hub calendar: common chain → hub scenes chained by `next`,
     each with `uses: [hub_choice]`; spokes `<id>_sNN` with `next: null`;
     finales `<id>_finale`; a fallback lock with `links:` to every finale
     and the alone ending. Generate `includes/hub_choice.yaml` (the
     descending step ladder per route and the true-route question; see
     `projects/cym/includes/hub_choice.yaml` as the model) and declare
     `<id>_step: 0` and `<id>_true: false` per route in `flags:`.
   Every scene carries `intimacy:` (0–5) consistent with the route's ladder
   in `arcs.md`; the rating follows the level (0 sfw, 1–2 suggestive, 3–5
   explicit). Unwritten scenes declare their outgoing edges with `links:` /
   `uses:` so `vn validate` can check reachability before writing.
   For a long outline, generate `outline.yaml` from a script that holds the
   data (keep it out of the repo); hand-edit the YAML afterwards.
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
