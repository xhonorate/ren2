"""Jobs that run on the CPU: validate, build, and Claude Code (revise, review images)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from vn.compile_renpy import build
from vn.project import validate

from ..queue import Cancelled


def _run(job, cmd: list[str], cwd: Path, ctx) -> int:
    job.log("$ " + " ".join(cmd))
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout
    for line in proc.stdout:
        job.log(line.rstrip())
        if ctx.queue.is_cancelled(job["id"]):
            proc.kill(); raise Cancelled()
    return proc.wait()


def h_validate(job, ctx):
    p = ctx.project(job["payload"]["project"])
    errs = validate(p)
    for e in errs:
        job.log("ERROR " + e)
    return {"errors": errs, "valid": not errs}


def h_build(job, ctx):
    pl = job["payload"]
    p = ctx.project(pl["project"])
    errs = validate(p)
    if errs:
        raise RuntimeError(f"{len(errs)} validation error(s); fix them first")
    out = p.root / "build"
    template = Path(pl["template"]) if pl.get("template") else (Path(ctx.cfg["renpy_template"]) if ctx.cfg.get("renpy_template") else None)
    written = build(p, out, None, template, pl.get("start"))
    job.log(f"built {len(written)} scene(s) into {out / 'game'}")
    return {"out": str(out), "scenes": len(written)}


def h_launch(job, ctx):
    """Launch Ren'Py on the project's build (renpy_cmd in config), optionally at a scene."""
    pl = job["payload"]
    p = ctx.project(pl["project"])
    cmd = ctx.cfg.get("renpy_cmd")
    if not cmd:
        raise RuntimeError("set renpy_cmd in runner/config.yaml (path to renpy.sh / renpy.exe)")
    if pl.get("start"):
        h_build(job, ctx)
    subprocess.Popen([cmd, str(p.root / "build")])
    return {"launched": True}


def _claude(job, ctx, prompt: str, cwd: Path, extra: list[str] | None = None) -> str:
    cmd = ctx.cfg.get("claude_cmd", "claude")
    if not shutil.which(cmd):
        raise RuntimeError(f"{cmd!r} not found on PATH; install Claude Code or set claude_cmd")
    args = [cmd, "-p", prompt, "--permission-mode", "acceptEdits"] + (extra or ctx.cfg.get("claude_args", []))
    job.log("$ claude -p ... (" + prompt[:80].replace("\n", " ") + ")")
    proc = subprocess.Popen(args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout
    out = []
    for line in proc.stdout:
        out.append(line); job.log(line.rstrip()[:300])
        if ctx.queue.is_cancelled(job["id"]):
            proc.kill(); raise Cancelled()
    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"claude exited {rc}")
    return "".join(out)


def h_revise(job, ctx):
    """Apply the human's review notes to a scene with the vn-revise skill."""
    pl = job["payload"]
    p = ctx.project(pl["project"])
    rel = p.root.relative_to(ctx.root)
    prompt = (f"Use the vn-revise skill: /vn-revise {rel} {pl['scene']}. Apply every unapplied note in "
              f"{rel}/review/notes/{pl['scene']}.yaml to {rel}/scenes/{pl['scene']}.yaml, re-validate, "
              f"mark the notes applied, and update the ledger entry if continuity changed.")
    out = _claude(job, ctx, prompt, ctx.root)
    return {"output": out[-4000:]}


def h_review_images(job, ctx):
    """Ask Claude Code to review a CG's candidates (or a sprite batch) against its spec; writes review.json."""
    pl = job["payload"]
    p = ctx.project(pl["project"])
    folder = p.root / pl["folder"]
    spec = folder / "spec.json"
    if not spec.exists():
        raise RuntimeError(f"no spec.json in {folder}")
    imgs = sorted(x.name for x in folder.glob("*.png"))
    if not imgs:
        raise RuntimeError("no images to review")
    rel = folder.relative_to(ctx.root)
    prompt = (f"You are reviewing generated visual-novel images against a spec. Read {rel}/spec.json, then look at each of "
              f"these images with the Read tool: {', '.join(str(rel / i) for i in imgs)}. For each image decide pass or fail "
              f"against the spec: right number of people, facing and framing per the shot, pose per the spec, outfit and "
              f"defining marks correct, background consistent, no text or artifacts. Write {rel}/review.json as a JSON list "
              f"of {{\"file\": name, \"verdict\": \"pass\"|\"fail\", \"reason\": one sentence, \"fix\": a prompt or seed note}}. "
              f"Do not edit anything else.")
    _claude(job, ctx, prompt, ctx.root)
    rev = folder / "review.json"
    return json.loads(rev.read_text(encoding="utf-8")) if rev.exists() else {"warning": "no review.json written"}


def register(q):
    q.register("validate", "local", h_validate)
    q.register("build", "local", h_build)
    q.register("launch", "local", h_launch)
    q.register("revise", "local", h_revise)
    q.register("review_images", "local", h_review_images)
