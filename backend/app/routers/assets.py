"""Serving and uploading content-addressed assets."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..jobs.queue import enqueue
from ..services import assets as asset_svc

router = APIRouter()

MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
    "wav": "audio/wav", "ogg": "audio/ogg", "mp3": "audio/mpeg", "flac": "audio/flac",
}


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


@router.get("/projects/{project_id}/gallery")
def gallery(project_id: str, kind: str = "", only_unused: bool = False,
            types: str = "", characters: str = "", scenes: str = "",
            used: str = "all", offset: int = 0, limit: int = 0) -> dict[str, Any]:
    """One page of generated assets, with what still references each.

    `types` / `characters` / `scenes` are comma-separated and OR within
    themselves, AND across each other. `used` is all|used|unused. `limit` 0
    returns everything -- the UI always pages, so a project with thousands of
    assets does not hand the browser thousands of image tags at once."""
    return asset_svc.inventory(
        project_id, kind or None, only_unused,
        types=_csv(types), characters=_csv(characters), scenes=_csv(scenes),
        used=used, offset=max(0, offset), limit=max(0, limit))


class RegenerateAsset(BaseModel):
    # One `regen.ref` from the asset's `used_by`. Empty means every
    # regenerable use of the file.
    ref: str = ""


@router.post("/projects/{project_id}/gallery/{asset_hash}/regenerate")
def regenerate_asset(project_id: str, asset_hash: str,
                     body: RegenerateAsset | None = None) -> dict[str, Any]:
    """Re-shoot an asset from the gallery, in place.

    Queues the same job the inspector would: the handler writes the new hash
    onto the node, character or location that pointed at the old one, so the
    content is replaced rather than added beside it. The old file stays in the
    store -- for a CG it is kept as a take on the node -- so this is not
    destructive.
    """
    targets = asset_svc.regen_targets(project_id, asset_hash, (body.ref if body else "") or None)
    if not targets:
        raise HTTPException(
            422, "nothing here can be regenerated -- an SFX, a voice reference "
                 "or a training-pool image comes from you, not from a generator")
    jobs = []
    for use in targets:
        regen = use["regen"]
        kind, label = asset_svc.REGEN_JOBS[regen["action"]]
        payload = dict(regen["payload"])
        jobs.append({
            "ref": regen["ref"],
            "action": regen["action"],
            "role": use.get("role", ""),
            "label": use.get("label", ""),
            "job_id": enqueue(project_id, kind, payload, label=label,
                              node_id=payload.get("node_id")),
        })
    return {"queued": jobs, "count": len(jobs),
            "job_ids": [j["job_id"] for j in jobs]}


@router.delete("/projects/{project_id}/gallery-unused")
def delete_unused_assets(project_id: str) -> dict[str, Any]:
    """Drop every asset nothing references, from disk and the index both.

    Not scoped to `project_id` -- an asset is only removed when no project uses
    it -- but the id keeps the route under the project's namespace like the
    rest of the gallery API."""
    return asset_svc.delete_unused()


@router.delete("/projects/{project_id}/gallery/{asset_hash}")
def delete_asset(project_id: str, asset_hash: str, force: bool = False) -> dict[str, Any]:
    result = asset_svc.delete(asset_hash, project_id, force)
    if not result["deleted"] and result.get("reason") == "still referenced":
        raise HTTPException(409, f"asset is still used in {len(result['used_by'])} place(s)")
    if not result["deleted"]:
        raise HTTPException(404, result.get("reason", "not found"))
    return result


@router.get("/assets/{asset_hash}")
def get_asset(asset_hash: str):
    path = asset_svc.path_for(asset_hash)
    if path is None:
        raise HTTPException(404, "asset not found")
    ext = path.suffix.lstrip(".").lower()
    return FileResponse(path, media_type=MIME.get(ext, "application/octet-stream"),
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/assets/{asset_hash}/meta")
def get_asset_meta(asset_hash: str) -> dict[str, Any]:
    meta = asset_svc.get_meta(asset_hash)
    if not meta:
        raise HTTPException(404, "asset not found")
    return meta


@router.post("/assets")
async def upload_asset(kind: str = "img", file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read()
    ext = (file.filename or "file.png").rsplit(".", 1)[-1].lower()
    digest = asset_svc.put(data, kind=kind, ext=ext, meta={"filename": file.filename})
    return {"hash": digest, "bytes": len(data), "ext": ext}
