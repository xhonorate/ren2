"""FastAPI entry point.

The browser never talks to ComfyUI or LM Studio directly (doc S6). This server
owns the filesystem, the queue, LoRA training and the Ren'Py compile step.
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import db, net, store
from .services import staleness
from .clients import comfyui
from .defaults import DEFAULT_SETTINGS
from .jobs import handlers  # noqa: F401 - registers job handlers
from .jobs import queue
from .routers import (assets, build, characters, config, generate, graph, jobs,
                      projects, roleplay, script)

app = FastAPI(title="VN Authoring Tool", version="1.0.0")

# Same-origin in production (the API and the built UI share a port); this only
# matters for the Vite dev server, and for a phone hitting it over the LAN.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=net.LAN_ORIGIN_RE.pattern,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(net.gate)

for router in (projects.router, graph.router, characters.router, generate.router,
               build.router, jobs.router, config.router, assets.router, script.router,
               roleplay.router):
    app.include_router(router, prefix="/api")

# The playable web build gets a bare /play/<pid>/ URL -- something you can open
# on a phone -- so it is mounted outside /api, and before the SPA catch-all.
app.include_router(build.play_router)

_worker: asyncio.Task | None = None


@app.on_event("startup")
async def startup() -> None:
    global _worker
    db.init_db()
    for key, value in DEFAULT_SETTINGS.items():
        if store.get_setting(key, None) is None:
            store.set_setting(key, value)
    store.migrate_scene_tone_to_nsfw()
    store.migrate_superseded_settings()
    store.migrate_superseded_templates()
    store.migrate_orphaned_sheet_batches()
    store.migrate_sprite_slots_to_poses()
    store.migrate_missing_default_outfit()
    staleness.migrate_voice_stamps()
    queue.reset_stuck_jobs()
    queue.set_loop(asyncio.get_running_loop())
    _worker = asyncio.create_task(queue.worker_loop())
    # Cache the ComfyUI node schema at startup; it is fine if it is not up yet.
    with contextlib.suppress(Exception):
        await asyncio.wait_for(comfyui.object_info(), timeout=4.0)


@app.on_event("shutdown")
async def shutdown() -> None:
    if _worker:
        _worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _worker


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {"ok": True, "data_dir": str(db.ROOT), "projects": len(store.list_projects())}


# --------------------------------------------------------------------------
# optional shared PIN, for serving over a network you do not fully trust
# --------------------------------------------------------------------------

@app.get("/api/auth/status")
async def auth_status(request: Request) -> dict[str, bool]:
    return {"required": net.auth_enabled(), "authorised": net.is_authorised(request)}


@app.post("/api/auth/login")
async def auth_login(request: Request) -> Response:
    body = await request.json()
    supplied = str(body.get("token", ""))
    expected = net.access_token()
    if expected is None:
        return JSONResponse({"ok": True, "required": False})
    if supplied != expected:
        return JSONResponse({"detail": "incorrect PIN"}, status_code=401)

    response = JSONResponse({"ok": True, "required": True})
    # Session cookie, so <img>, <audio> and EventSource authenticate too --
    # none of them can set a header.
    response.set_cookie(
        net.COOKIE_NAME, expected, httponly=True, samesite="lax", path="/",
    )
    return response


@app.post("/api/auth/logout")
async def auth_logout() -> Response:
    response = JSONResponse({"ok": True})
    response.delete_cookie(net.COOKIE_NAME, path="/")
    return response


# --------------------------------------------------------------------------
# built frontend, when it exists
# --------------------------------------------------------------------------

_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if _DIST.exists():
    # The API owns /api/*; everything else falls through to the SPA, including
    # Vite's hashed /assets/* bundles.
    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
