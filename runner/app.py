"""The review console API. Serves ui/ at / and project files at /files/<slug>/..."""
from __future__ import annotations

import json
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import Body, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from vn.assets import project_board, scene_assets
from vn.project import Project, validate
from vn.script import render_items

from .config import Config
from .handlers import register_all
from .queue import Queue

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


class Ctx:
    def __init__(self, cfg: Config, queue: Queue):
        self.cfg = cfg; self.queue = queue; self.root = ROOT
        self._cache: dict[tuple, Any] = {}

    def signature(self, d: Path) -> float:
        """Newest mtime among the files that change what the console shows."""
        latest = 0.0
        for sub in ("outline.yaml", "vocab.yaml", "ledger.yaml", "world.md", "premise.md"):
            f = d / sub
            if f.exists():
                latest = max(latest, f.stat().st_mtime)
        for sub in ("scenes", "ledger", "review/notes", "images/bg", "images/cg", "nsfw", "includes"):
            dd = d / sub
            if dd.exists():
                latest = max(latest, dd.stat().st_mtime, *(x.stat().st_mtime for x in dd.iterdir() if x.is_file()))
        for sub in ("audio/voice", "audio/takes", "review/cg", "characters"):
            dd = d / sub
            if dd.exists():
                latest = max(latest, dd.stat().st_mtime, *(x.stat().st_mtime for x in dd.rglob("*") if x.is_dir()))
        return latest

    def cached(self, slug: str, key: str, fn):
        d = self.projects().get(slug)
        if not d:
            raise HTTPException(404, f"no project {slug}")
        sig = self.signature(d)
        k = (slug, key)
        hit = self._cache.get(k)
        if hit and hit[0] == sig:
            return hit[1]
        val = fn()
        self._cache[k] = (sig, val)
        return val

    def projects(self) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for base in (ROOT / "projects", ROOT / "examples"):
            if base.exists():
                for d in sorted(base.iterdir()):
                    if (d / "outline.yaml").exists():
                        out.setdefault(d.name, d)
        return out

    def project(self, slug: str) -> Project:
        d = self.projects().get(slug)
        if not d:
            raise HTTPException(404, f"no project {slug}")
        return self.cached(slug, "project", lambda: Project.load(d))


def create_app(cfg: Optional[Config] = None, pin: Optional[str] = None) -> FastAPI:
    cfg = cfg or Config.load(ROOT / "runner" / "config.yaml")
    queue = Queue(ROOT / "runner" / "data" / "jobs.json", lanes=["images", "audio", "train", "local"])
    ctx = Ctx(cfg, queue)
    queue.ctx = ctx
    register_all(queue)
    queue.start()
    app = FastAPI(title="vn console")

    def warm():
        """First hits cost ~1-2 s per project; compute them once at startup, then on change."""
        import threading
        while True:
            for slug in list(ctx.projects()):
                try:
                    p = ctx.project(slug)
                    ctx.cached(slug, "board", lambda p=p: project_board(p))
                    ctx.cached(slug, "errors", lambda p=p: validate(p))
                except Exception:
                    pass
            time.sleep(15)
    import threading
    threading.Thread(target=warm, daemon=True, name="warm").start()
    token = secrets.token_urlsafe(16) if pin else None

    # ---- auth (LAN mode): a PIN exchanged once for a cookie, because <img>/<audio> cannot send headers
    @app.middleware("http")
    async def auth(request: Request, call_next):
        if token:
            path = request.url.path
            ok = (request.cookies.get("vn_session") == token or request.headers.get("x-vn-token") == token
                  or request.query_params.get("token") == token)
            if not ok and not (path == "/api/login" or path.startswith("/vendor") or path in ("/", "/index.html", "/app.js", "/app.css", "/views.js", "/api.js")):
                return JSONResponse({"error": "login required"}, status_code=401)
        return await call_next(request)

    @app.post("/api/login")
    def login(resp: Response, body: dict = Body(...)):
        if not token:
            return {"ok": True, "pin_required": False}
        if body.get("pin") != pin:
            raise HTTPException(403, "wrong PIN")
        resp.set_cookie("vn_session", token, httponly=True, samesite="lax", max_age=30 * 86400)
        return {"ok": True}

    @app.get("/api/session")
    def session(request: Request):
        return {"pin_required": bool(token), "authed": (not token) or request.cookies.get("vn_session") == token}

    # ---- projects
    @app.get("/api/projects")
    def projects():
        out = []
        for slug, d in ctx.projects().items():
            try:
                p = Project.load(d)
                written = len(p.scene_ids_written())
                out.append({"slug": slug, "path": str(d.relative_to(ROOT)), "title": p.premise.splitlines()[0].lstrip("# ").strip() if p.premise else slug,
                            "rating": p.rating(), "scenes": len(p.outline.scenes), "written": written,
                            "characters": list(p.characters)})
            except Exception as e:  # a broken project still shows up, with its error
                out.append({"slug": slug, "path": str(d.relative_to(ROOT)), "error": str(e)})
        return out

    @app.get("/api/projects/{slug}")
    def project(slug: str):
        p = ctx.project(slug)
        return {"slug": slug, "rating": p.rating(), "branching": p.outline.branching,
                "characters": {cid: {"name": c.appearance.display_name, "color": c.appearance.color,
                                     "outfits": list(c.appearance.outfits), "lora": c.appearance.lora.model_dump() if c.appearance.lora else None}
                               for cid, c in p.characters.items()},
                "locations": {lid: {"times": l.times} for lid, l in p.locations.items()},
                "vocab": {"expressions": list(p.vocab.expressions), "poses": list(p.vocab.poses), "cg_poses": list(p.vocab.cg_poses),
                          "shots": list(p.vocab.shots), "lighting": list(p.vocab.lighting), "deliveries": list(p.vocab.deliveries)},
                "board": ctx.cached(slug, "board", lambda: project_board(p))}

    @app.post("/api/projects/{slug}/validate")
    def validate_project(slug: str):
        p = ctx.project(slug)
        errs = ctx.cached(slug, "errors", lambda: validate(p))
        return {"valid": not errs, "errors": errs}

    # ---- scenes
    @app.get("/api/projects/{slug}/scenes/{sid}")
    def scene(slug: str, sid: str):
        p = ctx.project(slug)
        o = p.outline.by_id().get(sid)
        if not o:
            raise HTTPException(404, "not in outline")
        out: dict[str, Any] = {"id": sid, "outline": o.model_dump(), "written": sid in p.scene_ids_written(),
                               "notes": _read_notes(p, sid)}
        ids = [x.id for x in p.outline.scenes]
        i = ids.index(sid)
        out["prev"] = ids[i - 1] if i > 0 else None
        out["next"] = ids[i + 1] if i + 1 < len(ids) else None
        if out["written"]:
            try:
                sc = p.load_scene(sid)
                out.update(title=sc.title, location=sc.location, time=sc.time, present=sc.present, rating=sc.rating,
                           intimacy=sc.intimacy, items=render_items(p, sc), assets=scene_assets(p, sc),
                           errors=[e for e in ctx.cached(slug, "errors", lambda: validate(p)) if sid in e])
            except Exception as e:
                out["error"] = str(e)
        nsfw_dir = p.root / "nsfw"
        out["briefs"] = sorted(x.name for x in nsfw_dir.glob(f"{sid}.*.md")) if nsfw_dir.exists() else []
        return out

    @app.get("/api/projects/{slug}/scenes/{sid}/yaml")
    def scene_yaml(slug: str, sid: str):
        p = ctx.project(slug)
        f = p.scene_path(sid)
        if not f.exists():
            raise HTTPException(404)
        return {"yaml": f.read_text(encoding="utf-8")}

    @app.put("/api/projects/{slug}/scenes/{sid}/yaml")
    def scene_yaml_put(slug: str, sid: str, body: dict = Body(...)):
        p = ctx.project(slug)
        f = p.scene_path(sid)
        text = body.get("yaml", "")
        try:
            yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise HTTPException(400, f"not valid YAML: {e}")
        backup = p.root / "review" / "backup"; backup.mkdir(parents=True, exist_ok=True)
        if f.exists():
            shutil.copy2(f, backup / f"{sid}.{int(time.time())}.yaml")
        f.write_text(text, encoding="utf-8")
        return {"errors": validate(p, [sid])}

    @app.get("/api/projects/{slug}/scenes/{sid}/brief/{name}")
    def brief(slug: str, sid: str, name: str):
        p = ctx.project(slug)
        f = p.root / "nsfw" / name
        if not f.exists() or not name.startswith(sid):
            raise HTTPException(404)
        return {"markdown": f.read_text(encoding="utf-8")}

    # ---- notes (the human's review, consumed by /vn-revise)
    def _read_notes(p: Project, sid: str) -> list[dict]:
        f = p.root / "review" / "notes" / f"{sid}.yaml"
        if not f.exists():
            return []
        with open(f, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or []

    def _write_notes(p: Project, sid: str, notes: list[dict]) -> None:
        f = p.root / "review" / "notes" / f"{sid}.yaml"
        f.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "w", encoding="utf-8") as fh:
            yaml.safe_dump(notes, fh, sort_keys=False, allow_unicode=True)

    @app.post("/api/projects/{slug}/scenes/{sid}/notes")
    def add_note(slug: str, sid: str, body: dict = Body(...)):
        p = ctx.project(slug)
        notes = _read_notes(p, sid)
        note = {"id": secrets.token_hex(4), "step": body.get("step"), "kind": body.get("kind", "line"),
                "quote": (body.get("quote") or "")[:200], "note": body.get("note", "").strip(),
                "created": time.strftime("%Y-%m-%d %H:%M"), "applied": False}
        if not note["note"]:
            raise HTTPException(400, "empty note")
        notes.append(note); _write_notes(p, sid, notes)
        return notes

    @app.delete("/api/projects/{slug}/scenes/{sid}/notes/{nid}")
    def del_note(slug: str, sid: str, nid: str):
        p = ctx.project(slug)
        notes = [n for n in _read_notes(p, sid) if n.get("id") != nid]
        _write_notes(p, sid, notes)
        return notes

    # ---- characters and art
    @app.get("/api/projects/{slug}/characters/{cid}")
    def character(slug: str, cid: str):
        p = ctx.project(slug)
        c = p.characters.get(cid)
        if not c:
            raise HTTPException(404)
        art = c.dir / "art"
        def files(sub):
            d = art / sub
            return sorted(str(x.relative_to(p.root)) for x in d.glob("*.png")) if d.exists() else []
        voice_dir = c.dir / "voice"
        mining = {}
        if (voice_dir / "mining").exists():
            for d in sorted((voice_dir / "mining").iterdir()):
                mining[d.name] = sorted(str(x.relative_to(p.root)) for x in d.glob("*.wav"))
        def needs():
            need: dict[tuple, dict] = {}
            for sid in p.scene_ids_written():
                try:
                    for s in scene_assets(p, p.load_scene(sid))["sprites"]:
                        need.setdefault((s["char"], s["outfit"], s["pose"], s["expr"]), s)
                except Exception:
                    pass
            return need
        all_needs = ctx.cached(slug, "sprites", needs)
        sprites_needed = {k[1:]: v for k, v in all_needs.items() if k[0] == cid}
        return {"id": cid, "appearance": c.appearance.model_dump(), "sheet": c.sheet, "voice": c.voice,
                "candidates": files("candidates"), "canon": files("canon"), "rejected": files("rejected"),
                "sprites": sorted(sprites_needed.values(), key=lambda s: (s["outfit"], s["pose"], s["expr"])),
                "mining": mining, "refs": sorted(str(x.relative_to(p.root)) for x in (voice_dir / "ref").glob("*.wav")) if (voice_dir / "ref").exists() else [],
                "source_clips": len(list((voice_dir / "source").glob("*"))) if (voice_dir / "source").exists() else 0}

    @app.post("/api/projects/{slug}/characters/{cid}/cull")
    def cull(slug: str, cid: str, body: dict = Body(...)):
        """Move a candidate to canon/ or rejected/."""
        p = ctx.project(slug)
        src = _safe(p.root, body["file"])
        dest = p.root / "characters" / cid / "art" / ("canon" if body.get("keep") else "rejected") / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        side = src.with_suffix(".json")
        if side.exists():
            shutil.move(str(side), str(dest.with_suffix(".json")))
        return {"moved": str(dest.relative_to(p.root))}

    @app.post("/api/projects/{slug}/characters/{cid}/sprite-verdict")
    def sprite_verdict(slug: str, cid: str, body: dict = Body(...)):
        """fail = delete the sprite (and queue a re-render if asked); pass = keep."""
        p = ctx.project(slug)
        f = _safe(p.root, body["file"])
        if body.get("verdict") == "fail":
            rej = p.root / "characters" / cid / "art" / "rejected"; rej.mkdir(parents=True, exist_ok=True)
            if f.exists():
                shutil.move(str(f), str(rej / f"{int(time.time())}_{f.name}"))
            if body.get("regenerate"):
                queue.submit("sprite_sheet", {"project": slug, "char": cid, "items": [body["item"]]}, f"sprite {cid} {body['item']['pose']}/{body['item']['expr']}")
        return {"ok": True}

    @app.post("/api/projects/{slug}/characters/{cid}/pick-ref")
    def pick_ref(slug: str, cid: str, body: dict = Body(...)):
        """Promote a mined clip to the reference for a delivery tag."""
        p = ctx.project(slug)
        src = _safe(p.root, body["file"]); delivery = body["delivery"]
        dest = p.root / "characters" / cid / "voice" / "ref" / f"{delivery}.wav"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        vf = p.root / "characters" / cid / "voice.yaml"
        data = yaml.safe_load(vf.read_text(encoding="utf-8")) if vf.exists() else {}
        data.setdefault("reference", {})[delivery] = f"ref/{delivery}.wav"
        vf.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return {"reference": data["reference"]}

    # ---- CG review
    @app.get("/api/projects/{slug}/cgs")
    def cgs(slug: str):
        p = ctx.project(slug)

        def compute():
            out = []
            for sid in p.scene_ids_written():
                try:
                    for cg in scene_assets(p, p.load_scene(sid))["cgs"]:
                        rev = p.root / "review" / "cg" / cg["id"] / "review.json"
                        cg["review"] = json.loads(rev.read_text(encoding="utf-8")) if rev.exists() else None
                        out.append(cg)
                except Exception:
                    pass
            return out
        return ctx.cached(slug, "cgs", compute)

    @app.post("/api/projects/{slug}/cgs/{cgid}/pick")
    def pick_cg(slug: str, cgid: str, body: dict = Body(...)):
        p = ctx.project(slug)
        src = _safe(p.root, body["file"])
        dest = p.root / "images" / "cg" / f"{cgid}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return {"file": str(dest.relative_to(p.root))}

    # ---- voice takes
    @app.get("/api/projects/{slug}/voice/{sid}/{n}")
    def takes(slug: str, sid: str, n: int):
        p = ctx.project(slug)
        d = p.root / "audio" / "takes" / sid / f"{n:03d}"
        scores = d / "scores.json"
        return json.loads(scores.read_text(encoding="utf-8")) if scores.exists() else {"takes": [
            {"file": str(x.relative_to(p.root))} for x in sorted(d.glob("*.wav"))] if d.exists() else []}

    @app.post("/api/projects/{slug}/voice/{sid}/{n}/pick")
    def pick_take(slug: str, sid: str, n: int, body: dict = Body(...)):
        p = ctx.project(slug)
        src = _safe(p.root, body["file"])
        from .handlers.tts import _to_ogg
        dest = p.root / "audio" / "voice" / sid / f"{n:03d}.ogg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        for old in dest.parent.glob(f"{n:03d}.*"):
            old.unlink()
        final = _to_ogg(src, dest)
        return {"file": str(final.relative_to(p.root))}

    # ---- jobs
    @app.get("/api/jobs")
    def jobs():
        return [dict(j, log=j.get("log", [])[-5:]) for j in queue.list()]

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        j = queue.jobs.get(jid)
        if not j:
            raise HTTPException(404)
        return j

    @app.post("/api/jobs")
    def submit(body: dict = Body(...)):
        try:
            return queue.submit(body["kind"], body.get("payload", {}), body.get("label", ""))
        except KeyError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: str):
        return queue.cancel(jid) or {}

    @app.post("/api/jobs/{jid}/retry")
    def retry(jid: str):
        return queue.retry(jid) or {}

    @app.post("/api/jobs/clear")
    def clear():
        return {"removed": queue.remove_finished()}

    @app.post("/api/projects/{slug}/queue-missing")
    def queue_missing(slug: str, body: dict = Body(default={})):
        """Queue every missing asset of the chosen kinds for the chosen scenes (default: all written)."""
        p = ctx.project(slug)
        kinds = set(body.get("kinds") or ["backgrounds", "sprites", "cgs", "lines"])
        scenes = body.get("scenes") or p.scene_ids_written()
        made = []
        seen_bg, seen_char = set(), set()
        for sid in scenes:
            try:
                a = scene_assets(p, p.load_scene(sid))
            except Exception:
                continue
            if "backgrounds" in kinds:
                for b in a["backgrounds"]:
                    key = (b["location"], b["time"])
                    if not b["present"] and key not in seen_bg:
                        seen_bg.add(key); made.append(queue.submit("bg_plate", {"project": slug, "location": b["location"], "time": b["time"]}, f"bg {b['location']} {b['time']}")["id"])
            if "sprites" in kinds:
                for s in a["sprites"]:
                    if not s["present"] and s["char"] not in seen_char:
                        seen_char.add(s["char"]); made.append(queue.submit("sprite_sheet", {"project": slug, "char": s["char"]}, f"sprites {s['char']}")["id"])
            if "cgs" in kinds:
                for g in a["cgs"]:
                    if not g["present"] and not g["candidates"]:
                        made.append(queue.submit("cg", {"project": slug, "cg": g["id"]}, f"cg {g['id']}")["id"])
            if "lines" in kinds:
                missing = [l["n"] for l in a["lines"] if not l["present"]]
                if missing:
                    made.append(queue.submit("tts_line", {"project": slug, "scene": sid, "lines": missing}, f"voice {sid} ({len(missing)} lines)")["id"])
        return {"jobs": made}

    # ---- setup
    @app.get("/api/setup")
    def setup():
        from .handlers.comfy import ComfyClient
        import urllib.request
        checks = []
        checks.append({"name": "ComfyUI", "ok": ComfyClient(cfg["comfyui_url"]).alive(), "detail": cfg["comfyui_url"]})
        try:
            urllib.request.urlopen(cfg["tts_url"].rstrip("/") + "/", timeout=3); tts_ok = True
        except Exception:
            tts_ok = False
        checks.append({"name": "TTS server (GPT-SoVITS api_v2)", "ok": tts_ok, "detail": cfg["tts_url"]})
        checks.append({"name": "kohya sd-scripts", "ok": bool(cfg.get("kohya_path")) and (Path(cfg["kohya_path"]) / "sdxl_train_network.py").exists(), "detail": cfg.get("kohya_path") or "kohya_path not set"})
        checks.append({"name": "SDXL training base", "ok": bool(cfg.get("train_checkpoint")) and Path(cfg["train_checkpoint"]).exists(), "detail": cfg.get("train_checkpoint") or "train_checkpoint not set"})
        checks.append({"name": "ComfyUI LoRA folder", "ok": bool(cfg.get("comfyui_lora_dir")) and Path(cfg["comfyui_lora_dir"]).exists(), "detail": cfg.get("comfyui_lora_dir") or "comfyui_lora_dir not set"})
        checks.append({"name": "Claude Code CLI", "ok": bool(shutil.which(cfg.get("claude_cmd", "claude"))), "detail": cfg.get("claude_cmd")})
        checks.append({"name": "ffmpeg (ogg encoding)", "ok": bool(shutil.which("ffmpeg")), "detail": "optional; wav is used without it"})
        for mod, what in (("faster_whisper", "take scoring: word error rate"), ("resemblyzer", "take scoring: speaker similarity")):
            try:
                __import__(mod); ok = True
            except ImportError:
                ok = False
            checks.append({"name": mod, "ok": ok, "detail": what + " (optional)"})
        checks.append({"name": "Ren'Py", "ok": bool(cfg.get("renpy_cmd")) and Path(cfg["renpy_cmd"]).exists(), "detail": cfg.get("renpy_cmd") or "renpy_cmd not set (optional; for Launch)"})
        return {"checks": checks, "config": dict(cfg), "config_path": str(cfg.path)}

    @app.put("/api/setup/config")
    def put_config(body: dict = Body(...)):
        for k, v in body.items():
            if k in cfg or k in ("renpy_cmd", "renpy_template", "claude_args", "cg_candidates"):
                cfg[k] = v
        cfg.save()
        return {"saved": str(cfg.path)}

    # ---- files
    @app.get("/files/{slug}/{path:path}")
    def file(slug: str, path: str):
        p = ctx.project(slug)
        f = _safe(p.root, path)
        if not f.is_file():
            raise HTTPException(404)
        return FileResponse(str(f))

    app.mount("/", StaticFiles(directory=str(UI), html=True), name="ui")
    return app


def _safe(root: Path, rel: str) -> Path:
    f = (root / rel).resolve()
    if root.resolve() not in f.parents and f != root.resolve():
        raise HTTPException(400, "path outside project")
    return f
