"""Compile, preview, export, launch, and serve the web build."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from .. import store
from ..compiler.preview import compile_preview
from ..jobs.queue import enqueue
from ..services import build as build_svc

router = APIRouter()


@router.get("/projects/{project_id}/compile")
def compile_dry_run(project_id: str) -> dict[str, Any]:
    """Compile without writing -- the in-editor .rpy script view."""
    if store.get_project(project_id) is None:
        raise HTTPException(404, "project not found")
    return build_svc.dry_run(project_id)


@router.get("/projects/{project_id}/preview")
def preview_program(project_id: str, from_node: str = "") -> dict[str, Any]:
    """The second compile target: a program the browser player interprets.

    `from_node` makes any node directly playable, so testing a branch does not
    mean replaying the scenes that lead to it.
    """
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return compile_preview(
        project,
        store.list_nodes(project_id, include_orphaned=False),
        store.list_edges(project_id),
        store.list_characters(project_id),
        store.list_locations(project_id),
        start_node_id=from_node or None,
    )


@router.post("/projects/{project_id}/export")
def export(project_id: str, body: dict[str, Any] | None = None) -> dict[str, str]:
    body = body or {}
    return {"job_id": enqueue(project_id, "build.export", body, label="export Ren'Py project")}


@router.post("/projects/{project_id}/launch")
async def launch(project_id: str) -> dict[str, Any]:
    return await build_svc.launch(project_id)


@router.post("/renpy-launcher")
async def open_launcher() -> dict[str, Any]:
    """Open the SDK launcher GUI (no project argument -- that would run a game)."""
    return await build_svc.open_launcher()


@router.post("/projects/{project_id}/lint")
def lint(project_id: str) -> dict[str, str]:
    return {"job_id": enqueue(project_id, "build.lint", {}, label="renpy lint")}


@router.post("/projects/{project_id}/web-build")
def web_build(project_id: str, body: dict[str, Any] | None = None) -> dict[str, str]:
    """Queue a browser-playable build. Exports first unless told not to."""
    body = body or {}
    return {"job_id": enqueue(project_id, "build.web", body, label="web (HTML) build")}


@router.get("/projects/{project_id}/build-info")
def build_info(project_id: str) -> dict[str, Any]:
    exe = build_svc.renpy_executable()
    root = build_svc.project_dir(project_id)
    sdk = build_svc.renpy_sdk_root()
    stray = sdk / "game" if sdk else None
    return {
        "project_dir": str(root),
        "renpy_executable": str(exe) if exe else None,
        "validation": build_svc.validate(project_id),
        "web": build_svc.web_info(project_id),
        # Exporting into the SDK root shadows launcher/game and breaks the
        # launcher; warn before the export rather than after.
        "target_is_sdk": build_svc.is_renpy_sdk_root(root),
        "sdk_game_dir": str(stray) if stray and stray.is_dir() else None,
    }


# --------------------------------------------------------------------------
# serving the web build at /play/<project_id>/
# --------------------------------------------------------------------------
# Mounted without the /api prefix so the URL is something you can hand to a
# phone. Same reachability as the editor itself: on a LAN bind, anyone on the
# network can open it.

play_router = APIRouter()

# Windows' mimetypes registry does not know .wasm, and guessing wrong there
# stops the module from streaming-compiling.
_WEB_MIME = {
    ".html": "text/html", ".htm": "text/html", ".js": "text/javascript",
    ".mjs": "text/javascript", ".wasm": "application/wasm", ".json": "application/json",
    ".css": "text/css", ".zip": "application/zip", ".data": "application/octet-stream",
    ".mem": "application/octet-stream", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".ogg": "audio/ogg",
    ".opus": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".webm": "video/webm", ".ttf": "font/ttf", ".otf": "font/otf",
    ".woff": "font/woff", ".woff2": "font/woff2", ".txt": "text/plain",
}

# Ren'Py's web runtime uses SharedArrayBuffer, which browsers only hand to a
# cross-origin-isolated document.
_PLAY_HEADERS = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-cache",
}


def _serve(project_id: str, rel: str) -> FileResponse:
    root = build_svc.web_dir(project_id).resolve()
    target = (root / rel).resolve() if rel else root / "index.html"
    if target.is_dir():
        target = target / "index.html"
    # Never escape the staged build, whatever the path contains.
    if root not in target.parents and target != root:
        raise HTTPException(404, "not found")
    if not target.is_file():
        raise HTTPException(
            404,
            "no web build for this project yet -- run 'Build for web' in the Build view",
        )
    return FileResponse(target, media_type=_WEB_MIME.get(target.suffix.lower()),
                        headers=_PLAY_HEADERS)


@play_router.get("/play/{project_id}")
def play_root(project_id: str) -> RedirectResponse:
    # The trailing slash matters: index.html's asset URLs are relative to it.
    return RedirectResponse(f"/play/{project_id}/")


@play_router.get("/play/{project_id}/{rel:path}")
def play_asset(project_id: str, rel: str = "") -> FileResponse:
    return _serve(project_id, rel)
