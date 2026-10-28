---
name: vn-setup
description: (phase 2, GPU machine) Check and install the local models and services the job runner needs (ComfyUI checkpoint, ControlNet, LoRA trainer, TTS engine, Whisper, local LLM) from models.yaml.
---

# /vn-setup

Not implemented yet (phase 2). It will read `docs/models.md` / `models.yaml`,
check what is present on the machine (ComfyUI models dirs, the TTS engine,
the trainer, the local LLM server), download or print exact install steps
for what is missing, and write the resulting paths into `runner/config.yaml`.
