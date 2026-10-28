# Models (phase 2 — to be turned into models.yaml by /vn-setup)

| Job | Choice | Runs on | Notes |
|---|---|---|---|
| Design, prose, review | Claude (Opus-class for design/prose, Sonnet-class for extraction/ledger) | cloud / Claude Code | the whole point |
| Explicit prose fills | local uncensored roleplay model, largest that fits (12–24B class on the 3070, more with CPU offload) | 3070 or 5080 | narrow task from a complete brief |
| Sprites / CGs | Illustrious- or NoobAI-family SDXL + character LoRA + ControlNet OpenPose (Illustrious-compatible) | 5080 | see images.md |
| CG alternative | reference-editing model (Flux Kontext / Qwen-Image-Edit class) | 5080 | bake-off first |
| Background removal | rembg / BiRefNet node in ComfyUI | 5080 | sprites |
| LoRA training | kohya sd-scripts (see docs/legacy/lora-training.md; re-verify for Blackwell) | 5080, overnight | |
| Captioning | WD14 tagger | 5080 | |
| Image review | Claude (vision) | cloud | non-explicit |
| TTS | GPT-SoVITS fine-tune per character (fallback F5-TTS; IndexTTS2 for emotion ref) | 3070 | see tts.md |
| Take scoring | faster-whisper + ECAPA/Resemblyzer speaker embedding | 3070 | |

`/vn-setup` will check each, download what it can (HF hub / civitai URLs in
`models.yaml`), and print install steps for the rest with the exact target
directory.
