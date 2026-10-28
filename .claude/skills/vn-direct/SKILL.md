---
name: vn-direct
description: (phase 2, GPU machine) Render every CG a project's scenes specify, using pose references and background plates, with Claude reviewing non-explicit results and routing explicit ones to the human. Also runs nsfw fills through the local LLM.
---

# /vn-direct <project> [scene ...]

Not implemented yet (phase 2). The procedure (DESIGN.md §10, docs/images.md):

1. `python -m vn cgs projects/<slug>` is the job list. For each CG: the
   location plate for its location/time, the pose reference for each
   character's `cg_pose`, the shot and lighting tags, the character's LoRA
   and outfit tags. Render 4.
2. Review (non-explicit): right number of people, POV and framing per the
   shot, pose per the reference, outfit and marks correct, background
   consistent with the plate. Pick one or re-render with a note. Explicit
   CGs go to `projects/<slug>/review/` for the human; the automated checks
   still run.
3. nsfw fills: for every `nsfw/*.md` without `## Fill`, send its `## Prompt`
   to the local model, write the result under `## Fill` in scene-step YAML,
   run `vn validate`, then `/vn-review-scene` for the continuity check.
