"""Script view: linearized editing over the graph."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..graph import Graph, slugify
from ..models import DATA_MODELS, Edge, Node, NodeType
from ..services import placement, script as script_svc, staleness

router = APIRouter()


class ScriptQuery(BaseModel):
    start_id: Optional[str] = None
    choices: dict[str, str] = {}


@router.post("/projects/{project_id}/script")
def get_script(project_id: str, body: ScriptQuery) -> dict[str, Any]:
    """Rows for one path through the story, bound to their node ids."""
    nodes = store.list_nodes(project_id, include_orphaned=False)
    edges = store.list_edges(project_id)
    graph = Graph.build(nodes, edges)
    characters = {c.id: c for c in store.list_characters(project_id)}

    start_id = body.start_id
    if not start_id:
        scenes = graph.scenes()
        roots = graph.roots()
        start_id = scenes[0].id if scenes else (roots[0].id if roots else None)
    if not start_id or start_id not in graph.nodes:
        return {"rows": [], "taken": {}, "start": None, "labels": {},
                "scenes": _scene_list(graph)}

    out = script_svc.linearize(graph, start_id, characters, body.choices)
    out["scenes"] = _scene_list(graph)
    return out


def _scene_list(graph: Graph) -> list[dict[str, Any]]:
    return [{"id": s.id, "title": (s.data or {}).get("title", "Untitled scene")}
            for s in graph.scenes()]


@router.post("/projects/{project_id}/script/export")
def export_script(project_id: str, body: ScriptQuery) -> dict[str, str]:
    nodes = store.list_nodes(project_id, include_orphaned=False)
    edges = store.list_edges(project_id)
    graph = Graph.build(nodes, edges)
    characters = {c.id: c for c in store.list_characters(project_id)}
    start_id = body.start_id or (graph.scenes()[0].id if graph.scenes() else None)
    if not start_id:
        return {"text": ""}
    out = script_svc.linearize(graph, start_id, characters, body.choices)
    return {"text": script_svc.emit_script(out["rows"])}


class ParseBody(BaseModel):
    text: str


@router.post("/projects/{project_id}/script/parse")
def parse_script(project_id: str, body: ParseBody) -> dict[str, Any]:
    """Dry run: what a pasted block would become. Writes nothing."""
    characters = {c.id: c for c in store.list_characters(project_id)}
    locations = {l.id for l in store.list_locations(project_id)}
    return script_svc.parse_script(body.text, characters, locations)


class ImportBody(BaseModel):
    text: str
    anchor_id: str
    handle: str = "out"


@router.post("/projects/{project_id}/script/import")
def import_script(project_id: str, body: ImportBody) -> dict[str, Any]:
    """Parse a pasted block and splice the new nodes in after the anchor.

    Only ever creates nodes -- existing ones keep their ids, and therefore
    their Ren'Py labels.
    """
    anchor = store.get_node(body.anchor_id)
    if anchor is None:
        raise HTTPException(404, "anchor node not found")

    characters = {c.id: c for c in store.list_characters(project_id)}
    locations = {l.id for l in store.list_locations(project_id)}
    parsed = script_svc.parse_script(body.text, characters, locations)
    specs = parsed["nodes"]
    if not specs:
        return {"created": [], "warnings": parsed["warnings"], "count": 0}

    existing = next((e for e in store.list_edges(project_id)
                     if e.source == body.anchor_id and e.source_handle == body.handle), None)

    created: list[Node] = []
    for spec in specs:
        node_type = NodeType(spec["type"])
        data = DATA_MODELS[node_type].model_validate(spec["data"]).model_dump()
        created.append(Node(
            id=store.new_id("n_"), type=node_type, data=data,
            parent_id=anchor.id if anchor.type == NodeType.scene else anchor.parent_id,
            slug=slugify(_label_for(node_type, data), node_type.value),
        ))
    placement.stack(anchor, body.handle, created)
    placement.make_room(project_id, anchor, body.handle, created,
                        existing.target if existing else None)

    previous_id, previous_handle = body.anchor_id, body.handle
    for node in created:
        staleness.evaluate(node)
        store.insert_node(project_id, node)
        store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=previous_id,
                                           target=node.id, source_handle=previous_handle))
        previous_id, previous_handle = node.id, "out"

    reattached = None
    if existing:
        tail = created[-1]
        if tail.type == NodeType.choice:
            # Same rule as generation: a choice at the tail would strand what
            # followed, so every option converges on it until rewired.
            for opt in (tail.data or {}).get("options") or []:
                if opt.get("id"):
                    store.insert_edge(project_id, Edge(
                        id=store.new_id("e_"), source=tail.id, target=existing.target,
                        source_handle=opt["id"]))
            reattached = existing.target
        else:
            store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=tail.id,
                                               target=existing.target, source_handle="out"))
            reattached = existing.target

    return {
        "created": [n.model_dump() for n in created],
        "count": len(created),
        "warnings": parsed["warnings"],
        "reattached_to": reattached,
    }


def _label_for(node_type: NodeType, data: dict[str, Any]) -> str:
    if node_type == NodeType.scene:
        return data.get("title") or "scene"
    if node_type == NodeType.choice:
        return data.get("prompt") or "choice"
    return (data.get("text") or node_type.value)[:32]
