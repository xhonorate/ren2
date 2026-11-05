---
name: vn-voice
description: (GPU machine) Fine-tune each character's voice, mine emotion reference clips per delivery tag, and render + auto-score every voiced line of a vn project overnight.
---

# /vn-voice <project> [scene ...]

Runs on the GPU machine with the runner up (docs/tts.md).

1. Per character without `voice.yaml: model`: queue `voice_finetune`; it
   checks `voice/source/` and writes `voice/training.md` with the GPT-SoVITS
   steps (fine-tuning itself runs in its WebUI). Tell the human what is
   missing.
2. Per character and delivery tag with `emotion_ref: true` and no
   `reference.<tag>`: queue `emotion_mine`; the human picks on the cast
   page's voice tab (or you pick by listening to the scores' transcripts
   and `POST .../pick-ref`).
3. Queue `tts_line` per scene (the Voice page "Queue all missing voice").
   Each line renders N takes, scores them, keeps the best, and lists lines
   whose best is below threshold in the job result; re-queue those with a
   different delivery or more takes, and leave the rest to the human's
   takes picker in the scene reader.
