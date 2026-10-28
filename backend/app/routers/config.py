"""Prompt template and workflow configuration.

Templates are versioned, never overwritten, and diffable against the previous
version -- so you can A/B a change without losing the one that worked (doc S2).
"""
from __future__ import annotations

import difflib
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..defaults import DEFAULT_TEMPLATES, EMOTION_MAP
from ..models import PromptTemplate
from ..services import workflows

router = APIRouter()


class TemplateSave(BaseModel):
    body: str
    params: dict[str, Any] = {}


@router.get("/projects/{project_id}/templates")
def list_templates(project_id: str) -> dict[str, Any]:
    store.seed_templates(project_id)
    all_versions = store.list_templates(project_id)
    grouped: dict[str, list[PromptTemplate]] = {}
    for tpl in all_versions:
        grouped.setdefault(tpl.key, []).append(tpl)
    return {
        "keys": sorted(DEFAULT_TEMPLATES),
        "templates": {k: [t.model_dump() for t in v] for k, v in grouped.items()},
        "active": {k: next((t.model_dump() for t in v if t.active), None)
                   for k, v in grouped.items()},
    }


@router.get("/projects/{project_id}/templates/{key}")
def get_template(project_id: str, key: str) -> dict[str, Any]:
    versions = store.list_templates(project_id, key)
    if not versions and key not in DEFAULT_TEMPLATES:
        raise HTTPException(404, "unknown template key")
    active = store.active_template(project_id, key)
    return {
        "key": key,
        "active": active.model_dump() if active else None,
        "versions": [v.model_dump() for v in versions],
        "default": DEFAULT_TEMPLATES.get(key, {}),
    }


@router.post("/projects/{project_id}/templates/{key}")
def save_template(project_id: str, key: str, body: TemplateSave) -> dict[str, Any]:
    if key not in DEFAULT_TEMPLATES:
        raise HTTPException(400, f"unknown template key '{key}'")
    previous = store.active_template(project_id, key)
    saved = store.save_template_version(project_id, key, body.body, body.params)
    diff = list(difflib.unified_diff(
        (previous.body if previous else "").splitlines(),
        body.body.splitlines(),
        fromfile=f"{key}@v{previous.version if previous else 0}",
        tofile=f"{key}@v{saved.version}",
        lineterm="",
    ))
    return {"template": saved.model_dump(), "diff": diff}


@router.get("/projects/{project_id}/templates/{key}/diff")
def diff_template(project_id: str, key: str, a: int, b: int) -> dict[str, Any]:
    versions = {v.version: v for v in store.list_templates(project_id, key)}
    if a not in versions or b not in versions:
        raise HTTPException(404, "version not found")
    diff = list(difflib.unified_diff(
        versions[a].body.splitlines(), versions[b].body.splitlines(),
        fromfile=f"{key}@v{a}", tofile=f"{key}@v{b}", lineterm="",
    ))
    return {"diff": diff}


@router.post("/projects/{project_id}/templates/{key}/activate/{template_id}")
def activate(project_id: str, key: str, template_id: str) -> dict[str, bool]:
    store.activate_template(project_id, template_id)
    return {"activated": True}


@router.post("/projects/{project_id}/templates/{key}/reset")
def reset_template(project_id: str, key: str) -> dict[str, Any]:
    spec = DEFAULT_TEMPLATES.get(key)
    if spec is None:
        raise HTTPException(404, "unknown template key")
    saved = store.save_template_version(project_id, key, spec["body"], spec.get("params", {}))
    return saved.model_dump()


# --------------------------------------------------------------------------
# ComfyUI workflows + emotion map
# --------------------------------------------------------------------------

@router.get("/workflows")
def get_workflows() -> dict[str, Any]:
    stored = store.get_setting("workflows", {}) or {}
    return {
        "workflows": {name: stored.get(name, default)
                      for name, default in workflows.DEFAULT_WORKFLOWS.items()},
        "defaults": workflows.DEFAULT_WORKFLOWS,
        "node_classes": {**workflows.DEFAULT_NODE_CLASSES,
                         **(store.get_setting("node_classes", {}) or {})},
    }


@router.put("/workflows/{name}")
def put_workflow(name: str, body: dict[str, Any]) -> dict[str, Any]:
    if name not in workflows.DEFAULT_WORKFLOWS:
        raise HTTPException(400, f"unknown workflow '{name}'")
    stored = dict(store.get_setting("workflows", {}) or {})
    stored[name] = body
    store.set_setting("workflows", stored)
    return {"saved": name}


@router.delete("/workflows/{name}")
def reset_workflow(name: str) -> dict[str, Any]:
    stored = dict(store.get_setting("workflows", {}) or {})
    stored.pop(name, None)
    store.set_setting("workflows", stored)
    return {"reset": name}


@router.put("/node-classes")
def put_node_classes(body: dict[str, str]) -> dict[str, Any]:
    store.set_setting("node_classes", body)
    return {"saved": True}


@router.get("/emotion-map")
def get_emotion_map() -> dict[str, Any]:
    stored = store.get_setting("emotion_map", None)
    return {"map": stored or EMOTION_MAP, "default": EMOTION_MAP}


@router.put("/emotion-map")
def put_emotion_map(body: dict[str, Any]) -> dict[str, Any]:
    store.set_setting("emotion_map", body)
    return {"saved": True}
