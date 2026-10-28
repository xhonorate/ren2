---
name: vn-write-all
description: Write and review every unwritten scene of a vn project in story order, unattended, stopping only at the first-two-scenes checkpoint or on validation errors it cannot fix.
---

# /vn-write-all <project>

1. `python -m vn status projects/<slug>` to find the next unwritten scene.
2. For each unwritten scene in outline order: follow `/vn-write-scene` then
   `/vn-review-scene` exactly as written (read those skill files; do not
   abbreviate their steps). Always start a scene by running `vn pack` fresh.
3. Stop and show the human after the first two scenes of a new project.
   Otherwise do not stop; leave notes in `NOTES.md`.
4. Explicit scenes: write the brief and prompt, leave the fill for the GPU
   machine, continue. The ledger entry for such a scene records the state the
   brief specifies.
5. When done: `python -m vn validate`, then `python -m vn build`, then a
   summary: scenes written, voice fixes made, open threads, nsfw briefs
   awaiting fills.
