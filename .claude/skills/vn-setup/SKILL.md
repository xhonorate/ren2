---
name: vn-setup
description: (GPU machine) Check and install the local models and services the runner needs (ComfyUI checkpoint, kohya, GPT-SoVITS, Whisper, ffmpeg, Claude Code CLI) and fill runner/config.yaml.
---

# /vn-setup

1. Copy `runner/config.example.yaml` to `runner/config.yaml` if missing and
   ask the human for the paths it needs (or read them from the Setup page).
2. Run `python -m runner` and open `/api/setup`: every check must be `ok`.
   For each that is not, give the exact install step from `docs/models.md`
   and `docs/legacy/lora-training.md` (re-verify torch/xformers/bitsandbytes
   builds for the 5080), then re-check.
3. Verify one job of each kind on the demo project before touching a real
   one: `bg_plate`, `char_candidates`, `tts_line` (needs a reference clip),
   `lora_train` (needs 8+ canon images). Fix the workflow templates in
   `runner/workflows/` to match the installed ComfyUI nodes if a job fails.
