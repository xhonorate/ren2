---
name: vn-direct
description: (GPU machine) Render every CG a project's scenes specify, review non-explicit candidates against the spec and route explicit ones to the human; run nsfw fills through the local model.
---

# /vn-direct <project> [scene ...]

Runs on the GPU machine with the runner up.

1. `python -m vn cgs <project>` is the job list; queue a `cg` job per CG
   (the CGs page "Render all", or `POST /api/jobs`). Each writes four
   candidates and `spec.json` to `review/cg/<id>/`.
2. For non-explicit CGs, review the candidates with the Read tool against
   `spec.json` (people count, POV and framing per the shot, pose per the
   cg_pose, outfit and marks, background consistent with the plate, no
   text/artifacts) and write `review/cg/<id>/review.json` as a list of
   `{file, verdict, reason, fix}`; pick the best pass by copying it to
   `images/cg/<id>.png` (`POST .../cgs/<id>/pick`), or re-queue with the
   `fix` note folded into the prompt. Explicit CGs (`rating: explicit`)
   are left for the human on the CGs page; never open them.
3. nsfw fills: for every `nsfw/*.md` without `## Fill`, send its `## Prompt`
   plus the file named on its `Append:` line to the local model, write the
   result under `## Fill` as scene-step YAML, run `vn validate`, then
   `/vn-review-scene` for the continuity check. Do not read or edit the fill
   beyond the continuity check.
