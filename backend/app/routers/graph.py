"""Node and edge CRUD, layout, path state, validation, search."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..compiler.labels import compute_labels
from ..graph import Graph, resolve_state, slugify
from ..models import DATA_MODELS, Edge, Node, NodeType
from ..services import build as build_svc, pacing, placement, staleness

router = APIRouter()


class NodeCreate(BaseModel):
    type: NodeType
    data: dict[str, Any] = {}
    x: float = 0
    y: float = 0
    parent_id: Optional[str] = None
    slug: str = ""
    connect_from: Optional[str] = None
    connect_handle: str = "out"


class NodeUpdate(BaseModel):
    data: Optional[dict[str, Any]] = None
    x: Optional[float] = None
    y: Optional[float] = None
    parent_id: Optional[str] = None
    pinned_label: Optional[bool] = None
    slug: Optional[str] = None
    notes: Optional[str] = None
    orphaned: Optional[bool] = None
    active_variant: Optional[str] = None


class EdgeCreate(BaseModel):
    source: str
    target: str
    source_handle: str = "out"
    label: str = ""


class Positions(BaseModel):
    positions: list[dict[str, Any]]


@router.get("/projects/{project_id}/graph")
def get_graph(project_id: str, include_orphaned: bool = False) -> dict[str, Any]:
    nodes = store.list_nodes(project_id, include_orphaned=True)
    edges = store.list_edges(project_id)
    live = [n for n in nodes if not n.orphaned]
    graph = Graph.build(live, edges)
    labels = compute_labels(graph)
    scene_of: dict[str, str] = {}
    for node in live:
        owner = graph.owning_scene(node.id)
        if owner is not None:
            scene_of[node.id] = owner.id

    return {
        "nodes": [n.model_dump() for n in (nodes if include_orphaned else live)],
        "edges": [e.model_dump() for e in edges],
        "labels": labels,
        "scene_of": scene_of,
        "orphan_count": sum(1 for n in nodes if n.orphaned),
        "scenes": [{"id": s.id, "title": (s.data or {}).get("title", ""), "label": labels.get(s.id)}
                   for s in graph.scenes()],
    }


@router.post("/projects/{project_id}/nodes")
def create_node(project_id: str, body: NodeCreate) -> Node:
    payload_model = DATA_MODELS[body.type]
    data = payload_model.model_validate(body.data).model_dump()
    node = Node(
        id=store.new_id("n_"), type=body.type, data=data, x=body.x, y=body.y,
        parent_id=body.parent_id,
        slug=body.slug or slugify(_auto_label(body.type, data), body.type.value),
    )
    staleness.evaluate(node)
    store.insert_node(project_id, node)
    if body.connect_from:
        store.insert_edge(project_id, Edge(
            id=store.new_id("e_"), source=body.connect_from, target=node.id,
            source_handle=body.connect_handle,
        ))
    return node


def _auto_label(node_type: NodeType, data: dict[str, Any]) -> str:
    if node_type == NodeType.scene:
        return data.get("title") or "scene"
    if node_type == NodeType.choice:
        return data.get("prompt") or "choice"
    return (data.get("text") or node_type.value)[:32]


@router.patch("/projects/{project_id}/nodes/{node_id}")
def update_node(project_id: str, node_id: str, body: NodeUpdate) -> Node:
    node = store.get_node(node_id)
    if node is None:
        raise HTTPException(404, "node not found")
    update = body.model_dump(exclude_none=True)
    if "data" in update:
        merged = {**(node.data or {}), **update.pop("data")}
        node.data = DATA_MODELS[node.type].model_validate(merged).model_dump() | {
            k: v for k, v in merged.items() if k.startswith("_")
        }
    for field, value in update.items():
        setattr(node, field, value)
    # Editing text after generation is exactly what makes assets stale (doc S6).
    staleness.evaluate(node, staleness.voice_refs(project_id))
    return store.update_node(project_id, node)


@router.delete("/projects/{project_id}/nodes/{node_id}")
def delete_node(project_id: str, node_id: str, heal: bool = False,
                cascade: bool = False) -> dict[str, Any]:
    """Delete a node. With heal=true, reconnect its predecessors to its
    successor so removing a line mid-scene does not tear the path in two. With
    cascade=true, also delete every node reachable only through this one."""
    node = store.get_node(node_id)
    if node is None:
        raise HTTPException(404, "node not found")

    if cascade:
        graph = Graph.build(store.list_nodes(project_id, include_orphaned=False),
                            store.list_edges(project_id))
        removed = [node_id, *graph.strict_subtree(node_id)]
        for nid in removed:
            store.delete_node(project_id, nid)
        return {"deleted": True, "healed": [], "removed": removed}

    healed: list[str] = []
    if heal:
        edges = store.list_edges(project_id)
        incoming = [e for e in edges if e.target == node_id]
        outgoing = [e for e in edges if e.source == node_id]
        # Only a straight-line node can be healed: a Choice or Condition has
        # several successors and no single one to promote.
        successor = outgoing[0].target if len(outgoing) == 1 else None
        if successor and successor != node_id:
            for edge in incoming:
                if edge.source == successor:
                    continue
                store.insert_edge(project_id, Edge(
                    id=store.new_id("e_"), source=edge.source, target=successor,
                    source_handle=edge.source_handle))
                healed.append(f"{edge.source}:{edge.source_handle}")

    store.delete_node(project_id, node_id)
    return {"deleted": True, "healed": healed}


class InsertAfter(BaseModel):
    anchor_id: str
    handle: str = "out"
    type: NodeType
    data: dict[str, Any] = {}
    slug: str = ""


@router.post("/projects/{project_id}/nodes/insert-after")
def insert_after(project_id: str, body: InsertAfter) -> dict[str, Any]:
    """Splice a node into an existing run, keeping whatever followed."""
    anchor = store.get_node(body.anchor_id)
    if anchor is None:
        raise HTTPException(404, "anchor node not found")

    existing = next((e for e in store.list_edges(project_id)
                     if e.source == body.anchor_id and e.source_handle == body.handle), None)

    data = DATA_MODELS[body.type].model_validate(body.data).model_dump()
    node = Node(
        id=store.new_id("n_"), type=body.type, data=data,
        parent_id=anchor.id if anchor.type == NodeType.scene else anchor.parent_id,
        slug=body.slug or slugify(_auto_label(body.type, data), body.type.value),
    )
    placement.stack(anchor, body.handle, [node])
    placement.make_room(project_id, anchor, body.handle, [node],
                        existing.target if existing else None)
    staleness.evaluate(node)
    store.insert_node(project_id, node)

    store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=body.anchor_id,
                                       target=node.id, source_handle=body.handle))
    reattached = None
    if existing and existing.target != node.id:
        store.insert_edge(project_id, Edge(id=store.new_id("e_"), source=node.id,
                                           target=existing.target, source_handle="out"))
        reattached = existing.target

    return {"node": node.model_dump(), "reattached_to": reattached}


class RestoreBody(BaseModel):
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []


@router.post("/projects/{project_id}/nodes/restore")
def restore(project_id: str, body: RestoreBody) -> dict[str, int]:
    """Re-create nodes and edges with their original ids.

    Undo has to restore the *same* node id: a new id would mean a new Ren'Py
    label, which would break every existing player save just because someone
    pressed Ctrl+Z.
    """
    for raw in body.nodes:
        node = Node.model_validate(raw)
        if store.get_node(node.id) is None:
            store.insert_node(project_id, node)
        else:
            store.update_node(project_id, node)
    for raw in body.edges:
        edge = Edge.model_validate(raw)
        if edge.source in {n["id"] for n in body.nodes} or store.get_node(edge.source):
            store.insert_edge(project_id, edge)
    return {"nodes": len(body.nodes), "edges": len(body.edges)}


@router.post("/projects/{project_id}/nodes/positions")
def move_nodes(project_id: str, body: Positions) -> dict[str, int]:
    store.move_nodes(project_id, body.positions)
    return {"moved": len(body.positions)}


@router.post("/projects/{project_id}/edges")
def create_edge(project_id: str, body: EdgeCreate) -> Edge:
    if body.source == body.target:
        raise HTTPException(400, "a node cannot connect to itself")
    edge = Edge(id=store.new_id("e_"), source=body.source, target=body.target,
                source_handle=body.source_handle, label=body.label)
    return store.insert_edge(project_id, edge)


@router.delete("/projects/{project_id}/edges/{edge_id}")
def delete_edge(project_id: str, edge_id: str) -> dict[str, bool]:
    store.delete_edge(project_id, edge_id)
    return {"deleted": True}


# --------------------------------------------------------------------------
# derived views
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/nodes/{node_id}/state")
def path_state(project_id: str, node_id: str) -> dict[str, Any]:
    """Resolved state at a node: where, who, wearing what, which flags."""
    graph = Graph.build(store.list_nodes(project_id, include_orphaned=False),
                        store.list_edges(project_id))
    chain = graph.path_to(node_id)
    state = resolve_state(chain, include_last=True)
    return {
        "state": state.to_dict(),
        "path": [{"id": n.id, "type": n.type.value,
                  "text": (n.data or {}).get("text", "")[:120]} for n in chain],
        "depth": len(chain),
    }


@router.get("/projects/{project_id}/validate")
def validate(project_id: str) -> dict[str, Any]:
    return build_svc.validate(project_id)


@router.get("/projects/{project_id}/search")
def search(project_id: str, q: str) -> list[dict[str, Any]]:
    """Full-text search -- needed from day one past ~200 nodes (doc S9)."""
    needle = (q or "").strip().lower()
    if not needle:
        return []
    hits: list[dict[str, Any]] = []
    for node in store.list_nodes(project_id):
        d = node.data or {}
        haystack = " ".join(str(v) for v in (
            d.get("text", ""), d.get("title", ""), d.get("prompt", ""),
            d.get("code", ""), node.slug, node.notes,
            " ".join(o.get("label", "") for o in (d.get("options") or [])),
        ))
        if needle in haystack.lower():
            hits.append({
                "id": node.id, "type": node.type.value, "slug": node.slug,
                "orphaned": node.orphaned, "x": node.x, "y": node.y,
                "excerpt": _excerpt(haystack, needle),
            })
    return hits[:200]


def _excerpt(text: str, needle: str, width: int = 90) -> str:
    idx = text.lower().find(needle)
    start = max(0, idx - width // 3)
    return text[start:start + width].strip()


@router.get("/projects/{project_id}/orphans")
def list_orphans(project_id: str) -> list[Node]:
    return [n for n in store.list_nodes(project_id) if n.orphaned]


@router.post("/projects/{project_id}/orphans/restore")
def restore_orphans(project_id: str, body: dict[str, list[str]]) -> dict[str, int]:
    ids = body.get("node_ids") or []
    store.set_orphaned(project_id, ids, False)
    return {"restored": len(ids)}


@router.delete("/projects/{project_id}/orphans")
def purge_orphans(project_id: str) -> dict[str, int]:
    removed = 0
    for node in store.list_nodes(project_id):
        if node.orphaned:
            store.delete_node(project_id, node.id)
            removed += 1
    return {"purged": removed}


@router.get("/projects/{project_id}/staleness")
def staleness_report(project_id: str) -> dict[str, Any]:
    return {**staleness.stale_nodes(project_id), **staleness.refresh_project(project_id)}


# --------------------------------------------------------------------------
# route / pacing
# --------------------------------------------------------------------------

@router.get("/projects/{project_id}/pacing")
def pacing_report(project_id: str) -> dict[str, Any]:
    """Every scene measured and grouped by route. Read-only; counts nothing new."""
    return pacing.report(project_id)


class RouteRename(BaseModel):
    old: str
    new: str


@router.post("/projects/{project_id}/routes/rename")
def rename_route(project_id: str, body: RouteRename) -> dict[str, int]:
    if not body.old.strip():
        raise HTTPException(400, "no route named")
    return {"updated": pacing.rename_route(project_id, body.old, body.new)}
