# Images (phase 2 notes)

Decisions from the design conversation; the runner implements these.

## Style and models
- Anime. Illustrious / NoobAI-family SDXL checkpoint for sprites and CGs;
  tag-conditioned, so `appearance.yaml` and `vocab.yaml` carry Danbooru-style
  tags. The old project's prompt-ordering notes (identity → outfit → pose →
  shot → lighting; LoRA via the loader node, never inline) are worth keeping
  but re-verify against the checkpoint actually installed.
- Prototype a reference-editing model (Flux Kontext / Qwen-Image-Edit class)
  for CGs: "this character (canon image), now <pose>, <shot>". It may beat a
  LoRA on marks and outfits. Decide after a bake-off on one character.
- Check what is current before installing anything; this moves monthly.

## The render contract
The image model renders; it never interprets. Every job is a structured spec:
- **Sprites**: character tags + outfit tags + `poses[id].tags` +
  `expressions[id]` + "simple background, white background, full body,
  looking at viewer" → transparent PNG via background removal. One LoRA per
  character (sprite LoRA trained on the canon set).
- **Background plates**: `world.md` `bg_prompt` + time-of-day tags, once per
  location × time, approved once, reused everywhere.
- **CGs**: `shots[id]` + `lighting[id]` + per-character tags + `cg_poses[id]`
  with its **ControlNet pose reference** + the **location plate** as img2img /
  depth condition. POV, pose and environment are forced by references, not
  requested by prompt. Multi-character CGs use a two-skeleton pose reference
  and regional prompting with one LoRA per region; keep them rare.
- Marks: `appearance.yaml` marks carry tags and placement; after canon art
  is approved, the spec is corrected to match the art.

## Review loop
Every job emits a contact sheet. Claude reviews non-explicit sheets against
the spec: count of people, facing, framing per shot, pose per reference,
outfit, marks, background consistency, sprite background cleanliness. Verdict
per image: pass / regenerate with a one-line prompt or seed note. Explicit
CGs go to the human's review folder; automated checks (face count,
transparency, resolution) still run on them.

## Pose library
`vocab.yaml: cg_poses[id].ref` points at an OpenPose/depth image, POV-framed.
Built once (from renders or stock skeletons) and reused across projects;
adding a pose means adding a reference.

## Hardware
5080 (16 GB): SDXL + ControlNet + LoRA comfortably; LoRA training at 1024px
batch 1 fits. Blackwell needs a recent CUDA/torch; verify xformers /
bitsandbytes builds before relying on the old training settings in
`docs/legacy/lora-training.md`.
