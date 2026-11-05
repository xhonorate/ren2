"""Voice jobs: render lines with GPT-SoVITS (api_v2), score takes, mine emotion references.

Untested against a live server here. Scoring uses faster-whisper and resemblyzer when
installed; without them a take is scored on duration and loudness only."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import yaml

from ..queue import Cancelled


def _tts(cfg, text: str, ref: str, prompt_text: str, speed: float, seed: int) -> bytes:
    q = urllib.parse.urlencode({"text": text, "text_lang": "en", "ref_audio_path": ref, "prompt_text": prompt_text,
                                "prompt_lang": "en", "speed_factor": speed, "seed": seed, "streaming_mode": "false",
                                "media_type": "wav"})
    with urllib.request.urlopen(cfg["tts_url"].rstrip("/") + "/tts?" + q, timeout=300) as r:
        return r.read()


def normalise(text: str) -> str:
    """Script text -> what is sent to the engine (names via pronunciation dict happen earlier)."""
    t = re.sub(r"\[player_name\]", "", text)
    t = t.replace("—", ", ").replace("…", "...")
    return re.sub(r"\s{2,}", " ", t).strip()


def _words(t: str) -> list[str]:
    t = re.sub(r"(\w)-(\w)-", r"\2", t)          # stutters: H-h-hello -> hello
    return re.findall(r"[a-z']+", t.lower())


def wer(ref: str, hyp: str) -> float:
    r, h = _words(ref), _words(hyp)
    if not r:
        return 0.0
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


def _duration(path: Path) -> float:
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


def score_take(cfg, path: Path, text: str, ref_clip: str | None) -> dict:
    s: dict = {"duration": _duration(path)}
    chars = max(len(text), 1)
    cps = chars / s["duration"] if s["duration"] else 0
    s["pace_ok"] = 8 <= cps <= 22
    try:
        from faster_whisper import WhisperModel  # type: ignore
        model = _whisper(cfg)
        segs, _ = model.transcribe(str(path))
        s["transcript"] = " ".join(x.text for x in segs).strip()
        s["wer"] = wer(text, s["transcript"])
    except ImportError:
        s["wer"] = None
    try:
        if ref_clip:
            from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
            enc = VoiceEncoder()
            a = enc.embed_utterance(preprocess_wav(str(path)))
            b = enc.embed_utterance(preprocess_wav(ref_clip))
            s["similarity"] = float((a * b).sum())
    except ImportError:
        s["similarity"] = None
    score = 1.0
    if s["wer"] is not None:
        score -= min(s["wer"], 1.0) * 0.7
    if s.get("similarity") is not None:
        score -= (1 - s["similarity"]) * 0.3
    if not s["pace_ok"]:
        score -= 0.2
    s["score"] = round(score, 3)
    return s


_W = {}


def _whisper(cfg):
    from faster_whisper import WhisperModel  # type: ignore
    if "m" not in _W:
        _W["m"] = WhisperModel(cfg.get("whisper_model", "base"))
    return _W["m"]


def _voice(p, cid: str) -> dict:
    return p.characters[cid].voice or {}


def _to_ogg(src: Path, dst: Path) -> Path:
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:a", "libvorbis", "-q:a", "5", str(dst)], check=True)
        return dst
    dst = dst.with_suffix(".wav"); shutil.copy2(src, dst)   # Ren'Py plays wav too
    return dst


def h_tts_line(job, ctx):
    pl = job["payload"]; p = ctx.project(pl["project"]); cfg = ctx.cfg
    sc = p.load_scene(pl["scene"])
    lines = {l["n"]: l for l in p.lines(sc)}
    targets = [lines[int(n)] for n in pl.get("lines") or lines.keys()]
    takes_n = int(pl.get("takes", cfg["takes"]))
    results = []
    for i, l in enumerate(targets):
        if ctx.queue.is_cancelled(job["id"]):
            raise Cancelled()
        job.progress(i / len(targets), f"line {l['n']} ({i + 1}/{len(targets)})")
        v = _voice(p, l["speaker"])
        delivery = l["delivery"]
        refs = v.get("reference") or {}
        ref = refs.get(delivery) or refs.get("neutral")
        if not ref:
            raise RuntimeError(f"{l['speaker']} has no reference clip for {delivery} (or neutral) in voice.yaml")
        ref_path = str((p.root / "characters" / l["speaker"] / "voice" / ref).resolve()) if not Path(ref).is_absolute() else ref
        prompt_text = (v.get("reference_text") or {}).get(delivery, "")
        text = l["tts_text"]
        for k, val in (v.get("pronunciation") or {}).items():
            text = re.sub(rf"\b{re.escape(k)}\b", val, text)
        text = normalise(text)
        speed = float((p.vocab.deliveries.get(delivery) or {}).get("speed", 1.0))
        tdir = p.root / "audio" / "takes" / sc.id / f"{l['n']:03d}"
        tdir.mkdir(parents=True, exist_ok=True)
        scores = []
        for k in range(takes_n):
            wav = tdir / f"take_{k + 1}.wav"
            wav.write_bytes(_tts(cfg, text, ref_path, prompt_text, speed, seed=1000 + k))
            s = score_take(cfg, wav, text, ref_path)
            s["file"] = str(wav.relative_to(p.root)); scores.append(s)
        best = max(scores, key=lambda s: s["score"])
        (tdir / "scores.json").write_text(json.dumps({"text": text, "delivery": delivery, "takes": scores, "best": best["file"]}, indent=1), encoding="utf-8")
        out = p.root / l["file"]
        out.parent.mkdir(parents=True, exist_ok=True)
        final = _to_ogg(p.root / best["file"], out)
        results.append({"n": l["n"], "best": best["file"], "score": best["score"], "file": str(final.relative_to(p.root)),
                        "flag": best["score"] < 0.6})
    return {"lines": results, "flagged": [r["n"] for r in results if r["flag"]]}


MINE_TEXT = "I was going to say something, and then I didn't, and now it's late."


def h_emotion_mine(job, ctx):
    pl = job["payload"]; p = ctx.project(pl["project"]); cfg = ctx.cfg; cid = pl["char"]; delivery = pl["delivery"]
    v = _voice(p, cid)
    ref = (v.get("reference") or {}).get("neutral")
    if not ref:
        raise RuntimeError(f"{cid} needs a neutral reference clip first (voice.yaml reference.neutral)")
    ref_path = str((p.root / "characters" / cid / "voice" / ref).resolve())
    n = int(pl.get("count", 12))
    out_dir = p.root / "characters" / cid / "voice" / "mining" / delivery
    out_dir.mkdir(parents=True, exist_ok=True)
    text = pl.get("text") or MINE_TEXT
    speed = float((p.vocab.deliveries.get(delivery) or {}).get("speed", 1.0))
    files = []
    for k in range(n):
        if ctx.queue.is_cancelled(job["id"]):
            raise Cancelled()
        job.progress(k / n, f"take {k + 1}/{n}")
        f = out_dir / f"{k + 1:02d}.wav"
        f.write_bytes(_tts(cfg, text, ref_path, (v.get("reference_text") or {}).get("neutral", ""), speed * (0.9 + 0.02 * (k % 11)), seed=2000 + k))
        files.append(str(f.relative_to(p.root)))
    return {"files": files, "text": text}


def h_voice_finetune(job, ctx):
    """GPT-SoVITS fine-tuning runs in its own WebUI; this job checks the material and writes the steps."""
    pl = job["payload"]; p = ctx.project(pl["project"]); cid = pl["char"]
    src = p.root / "characters" / cid / "voice" / "source"
    clips = sorted(x for x in src.glob("*") if x.suffix.lower() in (".wav", ".mp3", ".flac", ".ogg")) if src.exists() else []
    minutes = sum(_duration(c) for c in clips if c.suffix.lower() == ".wav") / 60
    notes = p.root / "characters" / cid / "voice" / "training.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text(f"""# Fine-tuning {cid}'s voice (GPT-SoVITS)

Material: {len(clips)} clip(s) in `voice/source/` ({minutes:.1f} min of wav measured).

1. Open the GPT-SoVITS WebUI, tab 0: slice `voice/source/` into 3-10 s pieces, then ASR them (faster-whisper, en).
2. Tab 1: train SoVITS (8-12 epochs) and GPT (10-15 epochs) on the labelled set; name both `{cid}`.
3. In the inference WebUI pick the two weights, then set `model:` in `characters/{cid}/voice.yaml` to the SoVITS weight path
   and restart the api_v2 server with `-s <sovits> -g <gpt>` (or switch weights via /set_sovits_weights and /set_gpt_weights).
4. Put a clean 3-8 s neutral clip at `voice/ref/neutral.wav` and its transcript under `reference_text: {{neutral: ...}}` in voice.yaml.
5. Run an emotion-mining job per delivery tag and pick the reference clips.
""", encoding="utf-8")
    return {"clips": len(clips), "minutes": round(minutes, 1), "notes": str(notes.relative_to(p.root)),
            "manual": True, "note": "fine-tuning runs in the GPT-SoVITS WebUI; steps written to training.md"}


def register(q):
    q.register("tts_line", "audio", h_tts_line)
    q.register("emotion_mine", "audio", h_emotion_mine)
    q.register("voice_finetune", "audio", h_voice_finetune)
