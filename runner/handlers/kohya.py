"""LoRA training with kohya sd-scripts (sdxl_train_network.py). Untested here; the
arguments follow docs/legacy/lora-training.md and must be re-verified on a Blackwell card."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import yaml

from ..queue import Cancelled


def h_lora_train(job, ctx):
    pl = job["payload"]; p = ctx.project(pl["project"]); cid = pl["char"]; cfg = ctx.cfg
    a = p.characters[cid].appearance
    kohya = Path(cfg.get("kohya_path") or "")
    script = kohya / "sdxl_train_network.py"
    if not script.exists():
        raise RuntimeError("kohya_path must be the folder that contains sdxl_train_network.py (runner/config.yaml)")
    if not cfg.get("train_checkpoint"):
        raise RuntimeError("set train_checkpoint (an SDXL base .safetensors) in runner/config.yaml")
    canon = sorted((p.root / "characters" / cid / "art" / "canon").glob("*.png"))
    if len(canon) < 8:
        raise RuntimeError(f"need at least 8 canon images, have {len(canon)}")
    lo = {**cfg["lora"], **(pl.get("settings") or {})}
    trigger = pl.get("trigger") or (a.lora.trigger if a.lora else f"{cid}char")
    ds = ctx.root / "runner" / "data" / "lora" / cid
    img_dir = ds / "img" / f"{lo['repeats']}_{trigger}"
    if ds.exists():
        shutil.rmtree(ds)
    img_dir.mkdir(parents=True)
    # captions: trigger + the stable identity tags; outfit tags are left out so the LoRA learns the person, not the clothes
    caption = ", ".join(x for x in (trigger, a.base_tags, ", ".join(m.tags for m in a.marks if m.tags)) if x)
    for f in canon:
        shutil.copy2(f, img_dir / f.name)
        (img_dir / f.name).with_suffix(".txt").write_text(caption, encoding="utf-8")
    out_dir = ds / "out"; out_dir.mkdir()
    name = pl.get("name") or f"{cid}_v1"
    cmd = [cfg.get("kohya_python", "python"), str(script),
           "--pretrained_model_name_or_path", cfg["train_checkpoint"],
           "--train_data_dir", str(ds / "img"), "--output_dir", str(out_dir), "--output_name", name,
           "--resolution", f"{lo['resolution']},{lo['resolution']}", "--network_module", "networks.lora",
           "--network_dim", str(lo["dim"]), "--network_alpha", str(lo["alpha"]),
           "--learning_rate", str(lo["lr"]), "--max_train_epochs", str(lo["epochs"]), "--train_batch_size", "1",
           "--mixed_precision", "bf16", "--optimizer_type", "AdamW8bit", "--gradient_checkpointing", "--xformers",
           "--save_model_as", "safetensors", "--caption_extension", ".txt", "--cache_latents", "--enable_bucket"]
    job.log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=str(kohya), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout
    for line in proc.stdout:
        line = line.rstrip()
        m = re.search(r"steps:\s+(\d+)%", line)
        if m:
            job.progress(int(m.group(1)) / 100, line[:120])
        elif line:
            job.log(line[:300])
        if ctx.queue.is_cancelled(job["id"]):
            proc.kill(); raise Cancelled()
    if proc.wait() != 0:
        raise RuntimeError("training failed; see the log")
    result = out_dir / f"{name}.safetensors"
    if not result.exists():
        raise RuntimeError("training finished but no .safetensors was written")
    dest = p.root / "characters" / cid / "art" / "lora" / result.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result, dest)
    if cfg.get("comfyui_lora_dir"):
        shutil.copy2(result, Path(cfg["comfyui_lora_dir"]) / result.name)
        job.log(f"copied to {cfg['comfyui_lora_dir']}")
    # record on the character
    ap = p.root / "characters" / cid / "appearance.yaml"
    data = yaml.safe_load(ap.read_text(encoding="utf-8"))
    data["lora"] = {"file": result.name, "trigger": trigger, "weight": 0.8}
    ap.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {"lora": result.name, "trigger": trigger, "images": len(canon)}


def register(q):
    q.register("lora_train", "train", h_lora_train)
