"""ComfyUI image jobs: character candidates, sprite sheets, background plates, CGs.

Untested against a live ComfyUI in this repository's CI; the HTTP protocol is the
standard /prompt, /history/<id>, /view one. Workflows are API-format JSON templates
in runner/workflows/ with __PLACEHOLDER__ strings (see sdxl_txt2img.json).
"""
from __future__ import annotations

import json
import random
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from vn.assets import scene_assets

from ..queue import Cancelled

WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"


class ComfyClient:
    def __init__(self, url: str):
        self.url = url.rstrip("/")
        self.client_id = "vn-runner"

    def _json(self, path: str, data: dict | None = None) -> Any:
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())

    def alive(self) -> bool:
        try:
            self._json("/system_stats"); return True
        except Exception:
            return False

    def queue_prompt(self, workflow: dict) -> str:
        return self._json("/prompt", {"prompt": workflow, "client_id": self.client_id})["prompt_id"]

    def wait(self, prompt_id: str, job, ctx, timeout: float = 1800) -> list[dict]:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if ctx.queue.is_cancelled(job["id"]):
                raise Cancelled()
            hist = self._json(f"/history/{prompt_id}")
            if prompt_id in hist:
                outputs = hist[prompt_id].get("outputs", {})
                imgs = [im for node in outputs.values() for im in node.get("images", [])]
                if hist[prompt_id].get("status", {}).get("status_str") == "error":
                    raise RuntimeError("ComfyUI reported an error for this prompt")
                return imgs
            time.sleep(1.5)
        raise RuntimeError("timed out waiting for ComfyUI")

    def fetch(self, im: dict) -> bytes:
        q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""), "type": im.get("type", "output")})
        with urllib.request.urlopen(self.url + "/view?" + q, timeout=120) as r:
            return r.read()


def fill(template: dict, values: dict[str, Any]) -> dict:
    """Replace __KEY__ strings anywhere in the workflow; numeric values are inserted as numbers."""
    def rep(x):
        if isinstance(x, dict):
            return {k: rep(v) for k, v in x.items() if k != "_comment"}
        if isinstance(x, list):
            return [rep(v) for v in x]
        if isinstance(x, str) and x.startswith("__") and x.endswith("__"):
            key = x[2:-2]
            if key in values:
                return values[key]
        return x
    wf = rep(template)
    # no LoRA: bypass the LoraLoader by pointing its consumers at the checkpoint
    if not values.get("LORA"):
        for node in wf.values():
            for k, v in list(node.get("inputs", {}).items()):
                if isinstance(v, list) and len(v) == 2 and v[0] == "2":
                    node["inputs"][k] = ["1", v[1]]
        wf.pop("2", None)
    return wf


def render(job, ctx, prompt: str, negative: str, size: tuple[int, int], out: Path, seed: int | None = None,
           lora: dict | None = None, workflow: str = "sdxl_txt2img") -> Path:
    cfg = ctx.cfg
    client = ComfyClient(cfg["comfyui_url"])
    template = json.loads((WORKFLOWS / f"{workflow}.json").read_text(encoding="utf-8"))
    seed = seed if seed is not None else random.randint(1, 2**31)
    values = {"CHECKPOINT": cfg["checkpoint"], "PROMPT": prompt, "NEGATIVE": negative, "SEED": seed,
              "WIDTH": size[0], "HEIGHT": size[1], "STEPS": cfg["steps"], "CFG": cfg["cfg"],
              "SAMPLER": cfg["sampler"], "SCHEDULER": cfg["scheduler"], "PREFIX": "vn/" + out.stem,
              "LORA": (lora or {}).get("file", ""), "LORA_WEIGHT": (lora or {}).get("weight", 0.8)}
    wf = fill(template, values)
    job.log(f"prompt: {prompt[:160]}")
    pid = client.queue_prompt(wf)
    imgs = client.wait(pid, job, ctx)
    if not imgs:
        raise RuntimeError("ComfyUI returned no images")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(client.fetch(imgs[0]))
    (out.with_suffix(".json")).write_text(json.dumps({"prompt": prompt, "negative": negative, "seed": seed, "size": size,
                                                      "lora": lora, "workflow": workflow}, indent=1), encoding="utf-8")
    return out


def _char_prompt(p, cid: str, outfit: str | None, extra: str) -> tuple[str, str, dict | None]:
    a = p.characters[cid].appearance
    outfit = outfit or a.default_outfit
    o = a.outfits.get(outfit)
    marks = ", ".join(m.tags for m in a.marks if m.tags)
    parts = [a.base_tags, marks, o.tags if o else "", extra, a.style_tags or p.vocab.data.get("style_tags", "")]
    lora = a.lora.model_dump() if a.lora else None
    if lora and lora.get("trigger"):
        parts.insert(0, lora["trigger"])
    prompt = ", ".join(x for x in parts if x)
    negative = ", ".join(x for x in (a.negative_tags, "") if x)
    return prompt, negative, lora


def h_char_candidates(job, ctx):
    pl = job["payload"]; p = ctx.project(pl["project"]); cid = pl["char"]
    n = int(pl.get("count", 12)); cfg = ctx.cfg
    prompt, neg, lora = _char_prompt(p, cid, pl.get("outfit"),
                                     "1girl, solo, standing, full body, looking at viewer, simple background, white background")
    out_dir = p.root / "characters" / cid / "art" / "candidates"
    done = []
    for i in range(n):
        if ctx.queue.is_cancelled(job["id"]):
            raise Cancelled()
        job.progress(i / n, f"candidate {i + 1}/{n}")
        f = render(job, ctx, prompt, neg + ", " + cfg["negative_tags"], tuple(cfg["sprite_size"]),
                   out_dir / f"{int(time.time())}_{i:02d}.png", lora=lora)
        done.append(str(f.relative_to(p.root)))
    return {"files": done}


def _needed_sprites(p, cid: str) -> list[dict]:
    need: dict[tuple, dict] = {}
    for sid in p.scene_ids_written():
        for s in scene_assets(p, p.load_scene(sid))["sprites"]:
            if s["char"] == cid and not s["present"]:
                need[(s["outfit"], s["pose"], s["expr"])] = s
    return list(need.values())


def h_sprite_sheet(job, ctx):
    pl = job["payload"]; p = ctx.project(pl["project"]); cid = pl["char"]; cfg = ctx.cfg
    v = p.vocab
    need = _needed_sprites(p, cid) if not pl.get("items") else pl["items"]
    if not need:
        return {"files": [], "note": "no missing sprites for this character"}
    done = []
    for i, s in enumerate(need):
        if ctx.queue.is_cancelled(job["id"]):
            raise Cancelled()
        job.progress(i / len(need), f"{s['outfit']}/{s['pose']}/{s['expr']} ({i + 1}/{len(need)})")
        pose_tags = (v.poses.get(s["pose"]) or {}).get("tags", "")
        extra = ", ".join(x for x in ("1girl, solo, full body, looking at viewer, simple background, white background",
                                      pose_tags, v.expressions.get(s["expr"], "")) if x)
        prompt, neg, lora = _char_prompt(p, cid, s["outfit"], extra)
        f = render(job, ctx, prompt, neg + ", " + cfg["negative_tags"], tuple(cfg["sprite_size"]),
                   p.root / "characters" / cid / "art" / "sprites" / f"{s['outfit']}_{s['pose']}_{s['expr']}.png",
                   lora=lora, workflow=pl.get("workflow", "sdxl_txt2img"))
        done.append(str(f.relative_to(p.root)))
    return {"files": done, "note": "sprites are saved as rendered; add a background-removal node to the sprite workflow for transparency"}


TIME_TAGS = {"morning": "morning, soft sunlight", "afternoon": "afternoon, daylight", "evening": "evening, golden hour, warm light",
             "night": "night, dark, artificial lighting"}


def h_bg_plate(job, ctx):
    pl = job["payload"]; p = ctx.project(pl["project"]); cfg = ctx.cfg
    loc = p.locations[pl["location"]]; t = pl["time"]
    prompt = ", ".join(x for x in (loc.bg_prompt, TIME_TAGS.get(t, t), "no humans", cfg["style_tags"]) if x)
    f = render(job, ctx, prompt, cfg["negative_tags"] + ", 1girl, 1boy, people", tuple(cfg["bg_size"]),
               p.root / "images" / "bg" / f"{loc.id}_{t}.png", seed=pl.get("seed"))
    return {"file": str(f.relative_to(p.root))}


def h_cg(job, ctx):
    """Render N candidates for one CG into review/cg/<id>/ with spec.json; the reviewer picks."""
    pl = job["payload"]; p = ctx.project(pl["project"]); cfg = ctx.cfg; v = p.vocab
    spec = None
    for sid in p.scene_ids_written():
        for cg in p.cgs(p.load_scene(sid)):
            if cg["id"] == pl["cg"]:
                spec = cg
    if not spec:
        raise RuntimeError(f"cg {pl['cg']} not found in any written scene")
    loc = p.locations.get(spec["location"])
    parts = [v.shots.get(spec["shot"], ""), v.lighting.get(spec["lighting"], "")]
    lora = None
    for c in spec["characters"]:
        prompt_c, _, lora_c = _char_prompt(p, c["id"], c.get("outfit"), (v.cg_poses.get(c["pose"]) or {}).get("tags", "")
                                           + ", " + v.expressions.get(c["expr"], ""))
        parts.append(prompt_c); lora = lora or lora_c
    if loc:
        parts.append(loc.bg_prompt + ", " + TIME_TAGS.get(spec["time"], ""))
    parts.append(cfg["style_tags"])
    prompt = ", ".join(x for x in parts if x)
    out_dir = p.root / "review" / "cg" / spec["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
    n = int(pl.get("count", cfg.get("cg_candidates", 4)))
    files = []
    for i in range(n):
        if ctx.queue.is_cancelled(job["id"]):
            raise Cancelled()
        job.progress(i / n, f"candidate {i + 1}/{n}")
        f = render(job, ctx, prompt, cfg["negative_tags"], tuple(cfg["cg_size"]), out_dir / f"{int(time.time())}_{i:02d}.png",
                   lora=lora, workflow=pl.get("workflow", "sdxl_txt2img"))
        files.append(str(f.relative_to(p.root)))
    return {"files": files, "spec": spec, "explicit": spec.get("rating") == "explicit"}


def register(q):
    q.register("char_candidates", "images", h_char_candidates)
    q.register("sprite_sheet", "images", h_sprite_sheet)
    q.register("bg_plate", "images", h_bg_plate)
    q.register("cg", "images", h_cg)
