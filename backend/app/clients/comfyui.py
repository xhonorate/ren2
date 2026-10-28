"""ComfyUI client (headless, API only).

A workflow is a JSON graph, so templates carry {{placeholder}} tokens and we
inject prompt/seed/LoRA/dimensions per job (doc S4).

ComfyUI has no authentication -- bind it to LAN only. The browser never talks
to it directly; everything goes through this server.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Callable, Optional

import httpx
import websockets

from .. import store


class ComfyError(RuntimeError):
    pass


def base_url() -> str:
    return str(store.get_setting("comfyui_url", "http://127.0.0.1:8188")).rstrip("/")


def ws_url(client_id: str) -> str:
    url = base_url().replace("https://", "wss://").replace("http://", "ws://")
    return f"{url}/ws?clientId={client_id}"


_object_info_cache: dict[str, Any] = {}


async def object_info(refresh: bool = False) -> dict[str, Any]:
    """Node schema. Cached at startup (doc S4)."""
    if _object_info_cache and not refresh:
        return _object_info_cache
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base_url()}/object_info")
        r.raise_for_status()
    _object_info_cache.clear()
    _object_info_cache.update(r.json())
    return _object_info_cache


async def health() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(f"{base_url()}/system_stats")
            r.raise_for_status()
            stats = r.json()
        return {"ok": True, "url": base_url(), "stats": stats}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": base_url(), "error": str(exc)}


async def available(kind: str) -> list[str]:
    """Checkpoints / LoRAs / samplers the running ComfyUI actually has."""
    try:
        info = await object_info()
    except Exception:  # noqa: BLE001
        return []
    lookup = {
        "checkpoints": ("CheckpointLoaderSimple", "ckpt_name"),
        "loras": ("LoraLoader", "lora_name"),
        "samplers": ("KSampler", "sampler_name"),
        "schedulers": ("KSampler", "scheduler"),
        "vae": ("VAELoader", "vae_name"),
    }
    node, field = lookup.get(kind, (None, None))
    if not node or node not in info:
        return []
    required = info[node].get("input", {}).get("required", {})
    spec = required.get(field)
    if isinstance(spec, list) and spec and isinstance(spec[0], list):
        return list(spec[0])
    return []


async def queue_prompt(workflow: dict[str, Any], client_id: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{base_url()}/prompt",
                              json={"prompt": workflow, "client_id": client_id})
    if r.status_code >= 400:
        raise ComfyError(f"ComfyUI rejected the workflow ({r.status_code}): {r.text[:800]}")
    data = r.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise ComfyError(f"ComfyUI returned no prompt_id: {str(data)[:300]}")
    return prompt_id


async def history(prompt_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{base_url()}/history/{prompt_id}")
        r.raise_for_status()
    return r.json().get(prompt_id, {})


async def view(filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
    params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.get(f"{base_url()}/view", params=params)
        r.raise_for_status()
    return r.content


async def upload_image(data: bytes, filename: str, subfolder: str = "") -> dict[str, Any]:
    """Push reference images for img2img / ControlNet."""
    files = {"image": (filename, data, "image/png")}
    payload = {"overwrite": "true"}
    if subfolder:
        payload["subfolder"] = subfolder
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{base_url()}/upload/image", files=files, data=payload)
        r.raise_for_status()
    return r.json()


async def upload_audio(data: bytes, filename: str, subfolder: str = "") -> dict[str, Any]:
    """Push a timbre reference clip into ComfyUI's input/ dir.

    ComfyUI's LoadAudio only reads from its own input directory, and our clips
    live in the content-addressed store -- so a TTS job has to stage the clip
    here first. `/upload/image` is the same endpoint ComfyUI's own audio widget
    posts to; it takes any file under the `image` field. Returns
    {"name", "subfolder", "type"}.
    """
    files = {"image": (filename, data, "application/octet-stream")}
    payload = {"type": "input", "overwrite": "true"}
    if subfolder:
        payload["subfolder"] = subfolder
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{base_url()}/upload/image", files=files, data=payload)
        r.raise_for_status()
    return r.json()


async def free(unload_models: bool = True, free_memory: bool = True,
               attempts: int = 2, settle: float = 1.5) -> None:
    """VRAM recovery after OOM, and before handing the GPU to another model.

    Posted more than once, with a pause between, because one call is not reliably
    enough. Measured against a resident IndexTTS-2.5: the first `/free` returned
    200 and left 9.3 GB still held; a second call a moment later dropped it to
    3.0 GB. `/free` returns as soon as ComfyUI has *queued* the teardown, not when
    the allocator has given the memory back, and a custom node holding a model
    can outlive the first sweep.

    That matters because the caller is `vram._evict`, which reports success and
    lets the next job start -- so a premature return is how an image job ends up
    OOM-ing behind a voice model that was supposed to be gone.
    """
    for index in range(max(1, attempts)):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                await client.post(f"{base_url()}/free",
                                  json={"unload_models": unload_models,
                                        "free_memory": free_memory})
        except Exception:  # noqa: BLE001 - best effort
            return
        if index + 1 < attempts:
            await asyncio.sleep(settle)


async def interrupt() -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{base_url()}/interrupt")
    except Exception:  # noqa: BLE001
        pass


async def run_workflow(
    workflow: dict[str, Any],
    on_progress: Optional[Callable[[float, str], Any]] = None,
    timeout: float = 1200.0,
) -> dict[str, Any]:
    """Queue a workflow and follow it over the websocket until it finishes.

    Returns the history entry, from which outputs/filenames are read.
    """
    client_id = uuid.uuid4().hex

    try:
        async with websockets.connect(ws_url(client_id), max_size=64 * 1024 * 1024,
                                      open_timeout=15) as ws:
            prompt_id = await queue_prompt(workflow, client_id)
            current_node = ""
            deadline = asyncio.get_event_loop().time() + timeout

            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise ComfyError(f"ComfyUI job {prompt_id} timed out after {timeout}s")
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 60.0))
                except asyncio.TimeoutError:
                    # No traffic for a minute: fall back to polling history.
                    entry = await history(prompt_id)
                    if entry.get("outputs"):
                        return entry
                    continue

                if isinstance(raw, bytes):
                    continue  # preview frames
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue

                mtype = msg.get("type")
                data = msg.get("data", {})
                if data.get("prompt_id") not in (None, prompt_id):
                    continue

                if mtype == "executing":
                    node = data.get("node")
                    if node is None and data.get("prompt_id") == prompt_id:
                        return await history(prompt_id)
                    current_node = str(node)
                    if on_progress:
                        await _maybe_await(on_progress(0.05, f"node {current_node}"))
                elif mtype == "progress":
                    value, total = data.get("value", 0), max(1, data.get("max", 1))
                    if on_progress:
                        await _maybe_await(
                            on_progress(min(0.98, value / total), f"step {value}/{total}")
                        )
                elif mtype == "execution_error":
                    raise ComfyError(
                        f"ComfyUI execution error in node {data.get('node_type')}: "
                        f"{data.get('exception_message')}"
                    )
                elif mtype == "execution_cached" and on_progress:
                    await _maybe_await(on_progress(0.1, "cached"))
    except (OSError, websockets.exceptions.WebSocketException) as exc:
        raise ComfyError(f"ComfyUI websocket unreachable at {base_url()}: {exc}") from exc


async def _maybe_await(value: Any) -> None:
    if asyncio.iscoroutine(value):
        await value


def collect_images(entry: dict[str, Any]) -> list[dict[str, str]]:
    """Every image any SaveImage/PreviewImage node produced, in node order."""
    out: list[dict[str, str]] = []
    for node_id, node_out in (entry.get("outputs") or {}).items():
        for img in node_out.get("images", []) or []:
            out.append({
                "node": node_id,
                "filename": img.get("filename", ""),
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
            })
    return out


def collect_audio(entry: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for node_id, node_out in (entry.get("outputs") or {}).items():
        for key in ("audio", "audios"):
            for clip in node_out.get(key, []) or []:
                out.append({
                    "node": node_id,
                    "filename": clip.get("filename", ""),
                    "subfolder": clip.get("subfolder", ""),
                    "type": clip.get("type", "output"),
                })
    return out
