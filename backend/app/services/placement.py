"""Where new nodes land on the canvas.

Sizes and gaps mirror frontend/src/lib/layout.ts, so a generated chain turns
up about where auto-layout would put it instead of in a pile that has to be
laid out before it can be read.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .. import store
from ..graph import Graph
from ..models import Node, NodeType

NODE_W = 240
NODE_H = 96
THUMB_H = 96
COL_GAP = 60
CHAIN_GAP = 56
FAN_GAP = 90
# Space kept between a new card and one already on the canvas.
CLEARANCE = 20
# How many columns a chain may slide sideways looking for clear space.
MAX_SLIDE = 24

_BRANCHING = (NodeType.choice, NodeType.hub, NodeType.condition)


def _branch_test(branch: dict) -> bool:
    """Whether a condition branch tests anything; one that does not is the else."""
    gated = any((g.get("stat") or "").strip() for g in branch.get("requires") or [])
    return gated or bool((branch.get("expr") or "").strip())


def ports(node: Node) -> list[str]:
    """Bottom-edge port ids, left to right, as NodeCard's portsFor draws them."""
    d = node.data or {}
    if node.type == NodeType.choice:
        return [o["id"] for o in d.get("options") or [] if o.get("id")]
    if node.type == NodeType.hub:
        return [o["id"] for o in d.get("options") or []
                if o.get("id") and not o.get("target_route")]
    if node.type == NodeType.condition:
        branches = d.get("branches") or []
        ids = [b["id"] for b in branches if b.get("id")]
        if d.get("has_else") is not False and all(_branch_test(b) for b in branches):
            ids.append("else")
        return ids
    return []


def width_for(node: Node) -> float:
    d = node.data or {}
    if node.type in (NodeType.choice, NodeType.hub):
        count = max(1, len(d.get("options") or []))
    elif node.type == NodeType.condition:
        branches = d.get("branches") or []
        has_else = d.get("has_else") is not False and not any(not b.get("expr") for b in branches)
        count = max(1, len(branches) + (1 if has_else else 0))
    else:
        return NODE_W
    return max(NODE_W, count * 104 + 24)


def height_for(node: Node) -> float:
    base = NODE_H + 40 if node.type in _BRANCHING else NODE_H
    return base + THUMB_H if (node.data or {}).get("image_hash") else base


def column(anchor: Node, handle: str) -> tuple[float, float]:
    """Centre x and top y of the column that grows out of one of anchor's ports.

    A branching node's branches sit side by side under it in port order, one
    column each, centred on the node -- the first option's branch on the left,
    the last on the right -- far enough apart that branches generated one at a
    time land beside each other rather than on top of each other.
    """
    centre = anchor.x + width_for(anchor) / 2
    bottom = anchor.y + height_for(anchor)
    order = ports(anchor)
    if handle in order and len(order) > 1:
        slot = order.index(handle) - (len(order) - 1) / 2
        return centre + slot * (NODE_W + COL_GAP), bottom + FAN_GAP
    return centre, bottom + CHAIN_GAP


def stack(anchor: Node, handle: str, nodes: Iterable[Node]) -> None:
    """Position a chain hanging off anchor's handle, one card under the next."""
    centre, y = column(anchor, handle)
    for node in nodes:
        node.x = centre - width_for(node) / 2
        node.y = y
        y += height_for(node) + CHAIN_GAP


def _box(node: Node) -> tuple[float, float, float, float]:
    return node.x, node.y, node.x + width_for(node), node.y + height_for(node)


def _touch(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return (a[0] < b[2] + CLEARANCE and b[0] < a[2] + CLEARANCE
            and a[1] < b[3] + CLEARANCE and b[1] < a[3] + CLEARANCE)


def _scene(graph: Graph, node_id: str) -> Optional[str]:
    owner = graph.owning_scene(node_id)
    return owner.id if owner else None


def make_room(project_id: str, anchor: Node, handle: str, nodes: list[Node],
              downstream: Optional[str] = None) -> None:
    """Keep a chain that is about to be inserted off the cards already there.

    Call after `stack` and before inserting. Cards the chain is being spliced
    in front of -- `downstream` and what follows it -- are pushed down below
    the chain, the way inserting a line pushes the rest of the page down.
    Anything else in the way moves the chain instead: sideways, away from the
    middle of the fan, so a branch keeps its side. That is what a sub-choice
    deep in one branch needs when its outer branch runs into the next
    branch's column; auto-layout tidies it properly afterwards.
    """
    if not nodes:
        return
    graph = Graph.build(store.list_nodes(project_id, include_orphaned=False),
                        store.list_edges(project_id))
    scene = _scene(graph, anchor.id)
    fresh = {n.id for n in nodes}
    ahead = ({downstream, *graph.subtree(downstream)}
             if downstream and downstream in graph.nodes else set())
    below = anchor.y + height_for(anchor)

    in_scene = [n for n in graph.nodes.values()
                if n.id not in fresh and _scene(graph, n.id) == scene]
    # A loop back up to a hub above is not something to push down.
    pushed = [n for n in in_scene if n.id in ahead and n.y >= below]
    fixed = [_box(n) for n in in_scene if n.id not in ahead]

    order = ports(anchor)
    side = 0.0
    if handle in order and len(order) > 1:
        side = order.index(handle) - (len(order) - 1) / 2
    if side:
        steps = [step if side > 0 else -step for step in range(MAX_SLIDE + 1)]
    else:
        steps = [0] + [s for step in range(1, MAX_SLIDE + 1) for s in (step, -step)]

    pitch = NODE_W + COL_GAP
    home = [n.x for n in nodes]
    for step in steps:
        for node, x in zip(nodes, home):
            node.x = x + step * pitch
        if not any(_touch(_box(n), box) for n in nodes for box in fixed):
            break
    else:
        for node, x in zip(nodes, home):
            node.x = x

    if pushed and any(_touch(_box(n), _box(p)) for n in nodes for p in pushed):
        bottom = max(n.y + height_for(n) for n in nodes)
        shift = bottom + FAN_GAP - min(p.y for p in pushed)
        if shift > 0:
            store.move_nodes(project_id, [{"id": p.id, "x": p.x, "y": p.y + shift}
                                          for p in pushed])
