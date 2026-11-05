---
name: vn-art
description: (GPU machine) Produce canon character art, train the character LoRA and render the sprite sheet for one character, reviewing contact sheets; drives the runner's jobs and the console's cull/verdict files.
---

# /vn-art <project> <character id>

Runs on the GPU machine with the runner up (`python -m runner`). The console
(DESIGN.md §12) is where the human culls; this skill does the rest.

1. Queue `char_candidates` (24) via the API: `POST /api/jobs {kind, payload:
   {project, char, count}}` (or the character page's button). When done,
   review `characters/<id>/art/candidates/*.png` against `appearance.yaml`
   with the Read tool: one person, plain background, facing the viewer,
   marks present and placed right, outfit correct. Move passes to
   `art/canon/` and fails to `art/rejected/` with a one-line reason in
   `notes/<id>.md`; re-queue candidates with an adjusted `extra` prompt
   until 12–16 pass. Explicit renders never happen here.
2. **Checkpoint**: the human culls on the console's cast page. Then correct
   `appearance.yaml` to match the canon art (not the other way round).
3. Queue `lora_train` (the LoRA wizard, or the API). The job writes `lora:`
   into `appearance.yaml` and copies the file into ComfyUI's folder.
4. Queue `sprite_sheet`; review each sprite in `art/sprites/` against its
   vocab entry (pose tags, expression) and the outfit; fail with
   `sprite-verdict` (`POST /api/projects/<slug>/characters/<id>/sprite-verdict`
   with `regenerate: true`) and re-review.
5. Queue `bg_plate` for every location/time the written scenes need (the
   board's "queue missing" with only backgrounds ticked); review the plates.
