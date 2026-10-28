---
name: vn-art
description: (phase 2, GPU machine) Produce canon character art, train the character LoRA and render the sprite sheet for one character, with Claude reviewing contact sheets. Needs the job runner and ComfyUI.
---

# /vn-art <project> <character id>

Not implemented yet: the job runner (`runner/`) is built in phase 2 on the GPU
machine. The procedure it will follow (DESIGN.md §10, docs/images.md):

1. From `appearance.yaml`, render 24 candidates at a locked style and seed
   set (`char_candidates` job). Review the contact sheet against the spec:
   one person, plain background, facing the viewer, marks present and placed
   right. Mark pass/fail with a reason each; re-render the fails with the
   adjusted prompt. Repeat until 12–16 pass.
2. **Checkpoint**: the human culls to the canon set. Correct
   `appearance.yaml` to match the chosen art (not the other way round).
3. Queue `lora_train` (overnight batch). Write `lora:` into
   `appearance.yaml` when done.
4. Render the sprite sheet: every outfit × pose × the expressions the
   outline's scenes need (`vn lines`/scene files tell you which). Review
   each against the vocab entry; re-render fails. Save to
   `characters/<id>/art/sprites/<outfit>_<pose>_<expr>.png` (transparent).
5. Render each location's background plates (`bg_plate`) for the times it
   lists; save to `images/bg/<loc>_<time>.png`.
