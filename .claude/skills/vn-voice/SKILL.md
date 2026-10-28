---
name: vn-voice
description: (phase 2, GPU machine) Fine-tune each character's voice, mine emotion reference clips per delivery tag, and render + auto-score every voiced line of a vn project overnight.
---

# /vn-voice <project> [scene ...]

Not implemented yet (phase 2). The procedure (DESIGN.md §10, docs/tts.md):

1. Per character without `voice.yaml: model`: queue `voice_finetune` from the
   human's reference audio (overnight).
2. Per character and delivery tag with `emotion_ref: true` and no reference
   clip: `emotion_mine` renders a neutral line 12 ways with high variability;
   Claude or the human picks; the pick becomes `reference.<tag>`.
3. `python -m vn lines projects/<slug>` is the job list. Each line: apply the
   pronunciation dictionary and the engine's stutter/cut-off rules, render N
   takes with the delivery's reference clip and speed, score, keep the best
   at `audio/voice/<scene>/<nnn>.ogg`, list the lines whose best take is
   still below threshold in `NOTES.md`.
