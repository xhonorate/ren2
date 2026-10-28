"""Shared label computation.

Both compile targets -- Ren'Py and the web preview player -- run off exactly
this label set. If they disagreed, playtesting would stop predicting the build.
"""
from __future__ import annotations

from typing import Optional

from ..graph import Graph, label_name
from ..models import NodeType


def compute_labels(graph: Graph, warnings: Optional[list[str]] = None) -> dict[str, str]:
    """Label set, widened until every node is emitted exactly once.

    In-degree > 1 already covers merges and back-edges, so the widening pass
    only fires on a cycle with no entry point (A -> B -> A in isolation),
    which would otherwise be silently dropped.
    """
    labels = graph.label_nodes()
    for node in graph.nodes.values():
        d = node.data or {}
        if node.type == NodeType.choice and (d.get("loop_back") or d.get("exhaust")):
            labels.setdefault(node.id, label_name(node))

    for _ in range(64):
        covered = coverage(graph, labels)
        missing = sorted(set(graph.nodes) - covered)
        if not missing:
            break
        orphan = missing[0]
        labels[orphan] = label_name(graph.nodes[orphan])
        if warnings is not None:
            warnings.append(
                f"node {orphan} was only reachable inside a closed cycle; "
                f"promoted it to label {labels[orphan]}"
            )
    return labels


def coverage(graph: Graph, labels: dict[str, str]) -> set[str]:
    covered: set[str] = set()
    for start in labels:
        walk_run(graph, start, labels, covered)
    return covered


def walk_run(graph: Graph, start: str, labels: dict[str, str], covered: set[str]) -> None:
    """Which nodes a straight-line run from `start` swallows."""
    stack = [start]
    while stack:
        nid = stack.pop()
        if nid in covered or nid not in graph.nodes:
            continue
        covered.add(nid)
        node = graph.nodes[nid]
        if node.type in (NodeType.choice, NodeType.hub, NodeType.jump, NodeType.end):
            continue
        if node.type == NodeType.condition:
            for e in graph.out_edges(nid):
                if e.target not in labels:
                    stack.append(e.target)
            continue
        if node.type == NodeType.call:
            nxt = graph.successor(nid, "after")
            if nxt and nxt not in labels:
                stack.append(nxt)
            continue
        nxt = graph.default_successor(nid)
        if nxt and nxt not in labels and nxt != nid:
            stack.append(nxt)
