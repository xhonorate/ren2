"""Runner configuration: runner/config.yaml over these defaults."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    # services
    "comfyui_url": "http://127.0.0.1:8188",
    "comfyui_lora_dir": "",            # ComfyUI/models/loras; trained LoRAs are copied here
    "tts_url": "http://127.0.0.1:9880",  # GPT-SoVITS api_v2
    "claude_cmd": "claude",            # Claude Code CLI, for revise/review jobs
    # models
    "checkpoint": "waiILLUSTRIOUS_v140.safetensors",   # ComfyUI checkpoint name
    "train_checkpoint": "",            # SDXL base .safetensors path for kohya
    "kohya_path": "",                  # folder that contains sdxl_train_network.py
    "kohya_python": "python",          # python of kohya's venv
    "whisper_model": "base",           # faster-whisper model name, if installed
    # rendering
    "style_tags": "masterpiece, best quality, anime screencap, flat color",
    "negative_tags": "lowres, bad anatomy, bad hands, extra digits, text, watermark, worst quality, low quality, jpeg artifacts",
    "width": 1024, "height": 1024, "steps": 28, "cfg": 6.0, "sampler": "euler_ancestral", "scheduler": "normal",
    "sprite_size": [832, 1216], "bg_size": [1344, 768], "cg_size": [1344, 768],
    # lanes: which GPU each job family runs on (one job at a time per lane)
    "lanes": {"images": "cuda:0", "audio": "cuda:1", "train": "cuda:0", "local": "cpu"},
    # training defaults
    "lora": {"dim": 32, "alpha": 16, "epochs": 10, "lr": 1e-4, "resolution": 1024, "repeats": 10},
    "takes": 4,
}


class Config(dict):
    path: Path

    @classmethod
    def load(cls, path: Path | str = "runner/config.yaml") -> "Config":
        c = cls(DEFAULTS)
        c.path = Path(path)
        if c.path.exists():
            with open(c.path, encoding="utf-8") as f:
                c.update(yaml.safe_load(f) or {})
        return c

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(dict(self), f, sort_keys=False)
