"""LM Studio client (OpenAI-compatible /v1).

Two-pass generation lives on top of this (doc S2): pass 1 is unconstrained so
prose quality survives, pass 2 hands over a JSON schema, which LM Studio
enforces at the token level via llama.cpp grammar sampling.
"""
from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import httpx

from .. import store


class LMStudioError(RuntimeError):
    pass


def base_url() -> str:
    return str(store.get_setting("lm_studio_url", "http://127.0.0.1:1234")).rstrip("/")


async def list_models() -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(f"{base_url()}/v1/models")
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise LMStudioError(f"LM Studio unreachable at {base_url()}: {exc}") from exc
    return r.json().get("data", [])


def cli() -> str:
    """Path to the `lms` CLI, or "" if it is not on PATH.

    Unloading is the one thing the OpenAI-compatible endpoint cannot do -- /v1
    has no concept of residency -- and the documented ways in are the `lms`
    CLI, the Python SDK, or LM Studio's own idle TTL. The CLI wins here because
    it ships with the app and needs no extra dependency in this venv; `lms
    bootstrap` is what puts it on PATH.
    """
    configured = str(store.get_setting("lms_cli_path", "") or "").strip()
    if configured:
        return configured if shutil.which(configured) or Path(configured).exists() else ""
    return shutil.which("lms") or ""


async def loaded() -> list[dict[str, Any]]:
    """What LM Studio has in memory, with enough detail to put it back.

    /api/v0/models reports `state` and `loaded_context_length`, neither of
    which /v1/models has -- so this is the only way to tell "installed" from
    "resident", and the only way to reload a model at the context length it
    was actually running at rather than the model's default. Returns [] when
    the endpoint is missing (older builds) rather than guessing.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base_url()}/api/v0/models")
            if r.status_code >= 400:
                return []
            return [{"id": m.get("id", ""),
                     "context": m.get("loaded_context_length") or 0}
                    for m in (r.json().get("data") or [])
                    if m.get("state") == "loaded" and m.get("id")]
    except (httpx.HTTPError, ValueError):
        return []


async def loaded_models() -> list[str]:
    """Just the ids of what is resident."""
    return [m["id"] for m in await loaded()]


async def load(model_key: str, context: int = 0) -> tuple[bool, str]:
    """Load a model, at a given context length if one is known.

    The other half of `unload`, and not optional: LM Studio only JIT-loads
    when the request names a model, and this project's convention is that a
    blank `lm_studio_prose_model` means "whatever is loaded" -- so after an
    unload there is nothing for a blank-model request to load and it fails
    with a 400. Anything the CLI can take beyond the context length (a
    --parallel count, a --gpu ratio) goes in the `lms_load_args` setting.
    """
    binary = cli()
    if not binary:
        return False, "`lms` is not on PATH, so the LLM cannot be reloaded"
    if not model_key:
        return False, "no model to load: set a prose model in Config"

    args = [binary, "load", model_key, "-y"]
    if context:
        args += ["-c", str(int(context))]
    extra = str(store.get_setting("lms_load_args", "") or "").strip()
    if extra:
        args += shlex.split(extra)

    def run() -> subprocess.CompletedProcess:
        # Generous: a 24B off a cold SSD is the slow case, and failing the
        # load would fail a job that only needed to wait.
        return subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=600,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    try:
        result = await asyncio.get_running_loop().run_in_executor(None, run)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"`lms load {model_key}` failed: {exc}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:200]
        return False, f"`lms load {model_key}` exited {result.returncode}: {detail}"
    return True, f"loaded {model_key}"


async def unload() -> tuple[bool, str]:
    """Unload every model LM Studio has resident, freeing its VRAM.

    Returns (ok, note) instead of raising: the caller is a best-effort VRAM
    sweep in front of an image job, and a missing CLI must not stop rendering
    that would have worked anyway.
    """
    binary = cli()
    if not binary:
        return False, ("`lms` is not on PATH -- run `lms bootstrap` once, or set "
                       "lms_cli_path, so the LLM can be unloaded before image jobs")

    def run() -> subprocess.CompletedProcess:
        # utf-8/replace, not the locale codec: `lms` draws a spinner in
        # non-ASCII glyphs, and on a cp1252 console the default decode blows
        # up in the reader thread and takes the captured stderr with it -- so
        # a genuine failure would come back with no message to show.
        return subprocess.run([binary, "unload", "--all"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=60,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    try:
        result = await asyncio.get_running_loop().run_in_executor(None, run)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"`lms unload --all` failed: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[:200]
        return False, f"`lms unload --all` exited {result.returncode}: {detail}"
    return True, "unloaded the LLM"


async def health() -> dict[str, Any]:
    try:
        models = await list_models()
    except LMStudioError as exc:
        return {"ok": False, "url": base_url(), "error": str(exc), "models": [],
                "can_unload": bool(cli()), "loaded": []}
    return {"ok": True, "url": base_url(), "models": [m.get("id") for m in models],
            # Whether an automatic VRAM swap can actually evict this side.
            "can_unload": bool(cli()), "loaded": await loaded()}


def _body(messages: list[dict[str, str]], model: str, params: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "messages": messages,
        "stream": False,
        "temperature": params.get("temperature", 0.9),
        "max_tokens": params.get("max_tokens", 1200),
    }
    if model:
        body["model"] = model
    # top_k / top_p are disabled by default -- min_p does the work (doc S2).
    for key in ("top_p", "top_k", "min_p", "presence_penalty", "frequency_penalty", "seed"):
        if params.get(key) is not None:
            body[key] = params[key]
    if params.get("repetition_penalty") is not None:
        body["repeat_penalty"] = params["repetition_penalty"]
    for key in ("dry_multiplier", "dry_base", "dry_allowed_length", "xtc_probability",
                "xtc_threshold"):
        if params.get(key) is not None:
            body[key] = params[key]
    return body


async def chat(
    messages: list[dict[str, str]],
    model: str = "",
    params: Optional[dict[str, Any]] = None,
    json_schema: Optional[dict[str, Any]] = None,
    schema_name: str = "response",
    timeout: float = 600.0,
) -> str:
    params = params or {}
    body = _body(messages, model, params)
    if json_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": json_schema},
        }

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.post(f"{base_url()}/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise LMStudioError(f"LM Studio request failed: {exc}") from exc
    if r.status_code >= 400:
        raise LMStudioError(f"LM Studio returned {r.status_code}: {r.text[:600]}")

    data = r.json()
    choices = data.get("choices") or []
    if not choices:
        raise LMStudioError(f"LM Studio returned no choices: {str(data)[:400]}")
    return (choices[0].get("message") or {}).get("content") or ""


async def chat_stream(
    messages: list[dict[str, str]],
    model: str = "",
    params: Optional[dict[str, Any]] = None,
    on_text: Optional[Callable[[str], Awaitable[bool]]] = None,
    timeout: float = 600.0,
) -> str:
    """`chat`, token by token.

    `on_text` gets the whole text so far after every chunk, and returning True
    ends the request there. Closing the connection is what tells LM Studio to
    stop sampling, so a reply cut off at the player's turn stops costing GPU
    time the moment it is cut -- not when the model would have finished.
    """
    body = _body(messages, model, params or {})
    body["stream"] = True
    text = ""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            async with client.stream("POST", f"{base_url()}/v1/chat/completions",
                                     json=body) as r:
                if r.status_code >= 400:
                    detail = (await r.aread()).decode("utf-8", errors="replace")
                    raise LMStudioError(f"LM Studio returned {r.status_code}: {detail[:600]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    if chunk.get("error"):
                        raise LMStudioError(f"LM Studio stream error: {str(chunk['error'])[:400]}")
                    choices = chunk.get("choices") or []
                    delta = ((choices[0].get("delta") or {}).get("content") or "") if choices else ""
                    if not delta:
                        continue
                    text += delta
                    if on_text is not None and await on_text(text):
                        break
    except httpx.HTTPError as exc:
        raise LMStudioError(f"LM Studio request failed: {exc}") from exc
    return text


async def count_tokens(text: str) -> int:
    """Rough token estimate. Good enough for context budgeting."""
    return max(1, int(len(text) / 3.6))
