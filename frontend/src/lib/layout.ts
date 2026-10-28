import type { IRNode } from "./types";

export const NODE_W = 240;
export const NODE_H = 96;

/** Space between neighbouring columns. */
export const COL_GAP = 60;
/** Space between a node and the single node after it. */
export const CHAIN_GAP = 56;
/** Space under a node that fans out, and above a merge point -- room for the
 *  edges to spread and for their labels. Mirrored by services/placement.py so
 *  a generated branch lands roughly where auto-layout would put it. */
export const FAN_GAP = 90;
/** Room kept in a row for a port whose edge runs past the row to a merge
 *  point below it, so that edge has a gap to run down. */
const LANE_W = 80;
const MARGIN = 40;
/** A virtual node above every root, so a scene with loose nodes still has a
 *  single tree to lay out. */
const ROOT = "__layout_root__";

export interface LayoutEdge {
  source: string;
  target: string;
  source_handle?: string;
}

export interface Size {
  width?: number;
  height?: number;
}

interface Extent {
  left: number;
  right: number;
  bottom: number;
}

/**
 * Auto-layout, top to bottom. Essential past ~100 nodes (doc S7).
 *
 * Laid out the way a story branches rather than as a generic graph: a node's
 * branches sit side by side under it in the order its ports are listed, each
 * branch in its own column wide enough for everything nested inside it, and
 * the point where branches merge again sits centred under the node that split
 * them, below the longest branch. That is a dominator tree -- a merge point
 * belongs to the nearest node every path to it passes through -- and it is
 * what keeps branch A's own sub-choices from spilling into branch B's column,
 * which a crossing-minimising layout with no notion of port order cannot
 * promise.
 *
 * `measured` is the rendered size of each card where React Flow has one. The
 * estimates below are a floor: a card with three lines of text and a label is
 * taller than NODE_H, and laying it out as NODE_H is how cards end up
 * overlapping.
 *
 * Edges that loop back up the story are drawn but take no part in the layout.
 */
export function layoutGraph(
  nodes: IRNode[],
  edges: LayoutEdge[],
  measured: Record<string, Size | undefined> = {},
): { id: string; x: number; y: number }[] {
  if (!nodes.length) return [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const sizeOf = (id: string) => {
    if (id === ROOT) return { width: 0, height: 0 };
    const node = byId.get(id)!;
    return {
      width: Math.max(measured[id]?.width ?? 0, widthFor(node)),
      height: Math.max(measured[id]?.height ?? 0, heightFor(node)),
    };
  };

  // Each node's outgoing edges' targets, in the order its ports are listed:
  // one entry per edge in `byPort`, one per target in `outgoing`.
  const byPort = new Map<string, string[]>();
  const outgoing = new Map<string, string[]>();
  const ranked = new Map<string, { target: string; rank: number; seq: number }[]>();
  edges.forEach((edge, seq) => {
    const source = byId.get(edge.source);
    if (!source || !byId.has(edge.target) || edge.source === edge.target) return;
    const ports = portOrder(source);
    const at = ports.indexOf(edge.source_handle ?? "out");
    const list = ranked.get(edge.source) ?? [];
    list.push({ target: edge.target, rank: at < 0 ? ports.length : at, seq });
    ranked.set(edge.source, list);
  });
  for (const [id, list] of ranked) {
    list.sort((a, b) => a.rank - b.rank || a.seq - b.seq);
    byPort.set(id, list.map((e) => e.target));
    outgoing.set(id, [...new Set(byPort.get(id))]);
  }

  // Walk from the roots -- the scene first, then anything nothing leads to,
  // then whatever is only reachable around a loop -- dropping the edges that
  // close a loop, so what is left has an order to lay out in.
  const hasIncoming = new Set([...outgoing.values()].flat());
  const starts = [...nodes].sort((a, b) =>
    Number(hasIncoming.has(a.id)) - Number(hasIncoming.has(b.id))
    || Number(b.type === "scene") - Number(a.type === "scene")
    || a.y - b.y || a.x - b.x);

  const forward = new Map<string, string[]>([[ROOT, []]]);
  const preds = new Map<string, string[]>();
  const link = (from: string, to: string) => {
    forward.get(from)!.push(to);
    const list = preds.get(to);
    if (list) list.push(from);
    else preds.set(to, [from]);
  };
  const open = new Set<string>();
  const finished: string[] = [];
  for (const start of starts) {
    if (forward.has(start.id)) continue;
    forward.set(start.id, []);
    link(ROOT, start.id);
    open.add(start.id);
    const stack: [string, number][] = [[start.id, 0]];
    while (stack.length) {
      const frame = stack[stack.length - 1];
      const targets = outgoing.get(frame[0]) ?? [];
      if (frame[1] === targets.length) {
        stack.pop();
        open.delete(frame[0]);
        finished.push(frame[0]);
        continue;
      }
      const next = targets[frame[1]++];
      if (open.has(next)) continue; // loops back up the story
      const seen = forward.has(next);
      link(frame[0], next);
      if (!seen) {
        forward.set(next, []);
        open.add(next);
        stack.push([next, 0]);
      }
    }
  }

  // Immediate dominators. Reverse finishing order is a topological order once
  // the loops are gone, so every predecessor is settled before its successor
  // and one pass is exact.
  const order = [ROOT, ...finished.reverse()];
  const index = new Map(order.map((id, i) => [id, i]));
  const idom = new Map<string, string>([[ROOT, ROOT]]);
  const meet = (a: string, b: string) => {
    while (a !== b) {
      while (index.get(a)! > index.get(b)!) a = idom.get(a)!;
      while (index.get(b)! > index.get(a)!) b = idom.get(b)!;
    }
    return a;
  };
  const owned = new Map<string, string[]>();
  for (const id of order.slice(1)) {
    const [first, ...rest] = preds.get(id)!;
    const owner = rest.reduce(meet, first);
    idom.set(id, owner);
    const list = owned.get(owner);
    if (list) list.push(id);
    else owned.set(owner, [id]);
  }

  // Where each node sits relative to its owner: centre-x offset, top offset.
  const offset = new Map<string, { dx: number; dy: number }>();
  const isBranch = (id: string) => preds.get(id)!.length === 1;

  const place = (id: string): Extent => {
    const { width, height } = sizeOf(id);
    const extent: Extent = { left: -width / 2, right: width / 2, bottom: height };
    const mine = new Set(owned.get(id) ?? []);

    // One slot per outgoing edge, in port order: the branch that hangs off
    // it, or an empty lane when the edge runs on to a merge point further
    // down, so that edge is not drawn straight through a neighbouring branch.
    const ahead = new Set(forward.get(id));
    const taken = new Set<string>();
    const slots = (id === ROOT ? forward.get(id)! : byPort.get(id) ?? [])
      .filter((t) => ahead.has(t))
      .flatMap((t) => {
        if (!mine.has(t) || !isBranch(t)) return [null];
        if (taken.has(t)) return [];
        taken.add(t);
        return [t];
      });
    if (slots.some(Boolean)) {
      const top = height + (slots.length > 1 ? FAN_GAP : CHAIN_GAP);
      const centres: number[] = [];
      const spans: Extent[] = [];
      let cursor = 0;
      let deepest = 0;
      slots.forEach((slot, i) => {
        if (i > 0) cursor += COL_GAP;
        const span = slot ? place(slot) : { left: -LANE_W / 2, right: LANE_W / 2, bottom: 0 };
        centres.push(cursor - span.left);
        spans.push(span);
        cursor += span.right - span.left;
        deepest = Math.max(deepest, span.bottom);
      });
      // Centred over its first and last branch, so the fan is symmetric.
      const mid = (centres[0] + centres[centres.length - 1]) / 2;
      slots.forEach((slot, i) => {
        const dx = centres[i] - mid;
        extent.left = Math.min(extent.left, dx + spans[i].left);
        extent.right = Math.max(extent.right, dx + spans[i].right);
        if (slot) offset.set(slot, { dx, dy: top });
      });
      extent.bottom = top + deepest;
    }

    // Merge points go below everything that leads into them, centred under
    // the node that split the paths.
    for (const join of mine) {
      if (isBranch(join)) continue;
      const span = place(join);
      const top = extent.bottom + FAN_GAP;
      offset.set(join, { dx: 0, dy: top });
      extent.left = Math.min(extent.left, span.left);
      extent.right = Math.max(extent.right, span.right);
      extent.bottom = top + span.bottom;
    }
    return extent;
  };
  place(ROOT);

  const at = new Map<string, { cx: number; top: number }>([[ROOT, { cx: 0, top: 0 }]]);
  for (const id of order.slice(1)) {
    const base = at.get(idom.get(id)!)!;
    const { dx, dy } = offset.get(id)!;
    at.set(id, { cx: base.cx + dx, top: base.top + dy });
  }

  let minX = Infinity;
  let minY = Infinity;
  for (const node of nodes) {
    const p = at.get(node.id)!;
    minX = Math.min(minX, p.cx - sizeOf(node.id).width / 2);
    minY = Math.min(minY, p.top);
  }
  return nodes.map((node) => {
    const p = at.get(node.id)!;
    return {
      id: node.id,
      x: Math.round(p.cx - sizeOf(node.id).width / 2 - minX + MARGIN),
      y: Math.round(p.top - minY + MARGIN),
    };
  });
}

/** Outgoing port ids in the order the card draws them, left to right. Mirrors
 *  NodeCard's portsFor, which this module cannot import without a cycle. */
export function portOrder(node: IRNode): string[] {
  const d = node.data ?? {};
  if (node.type === "choice") {
    return (d.options ?? []).map((o: any) => o?.id).filter(Boolean);
  }
  if (node.type === "hub") {
    return (d.options ?? []).filter((o: any) => !o?.target_route)
      .map((o: any) => o?.id).filter(Boolean);
  }
  if (node.type === "condition") {
    return [...(d.branches ?? []).map((b: any) => b?.id).filter(Boolean), "else"];
  }
  if (node.type === "call") return ["out", "after"];
  return ["out"];
}

/** Branch ports on Choice / Condition hang off the bottom edge, so those nodes
 *  grow sideways with the number of branches instead of downward (item 11). */
export function branchCount(node: IRNode): number {
  if (node.type === "choice" || node.type === "hub") {
    return Math.max(1, (node.data.options ?? []).length);
  }
  if (node.type === "condition") {
    const explicit = (node.data.branches ?? []).length;
    const hasElse = node.data.has_else !== false
      && !(node.data.branches ?? []).some((b: any) => !b.expr);
    return Math.max(1, explicit + (hasElse ? 1 : 0));
  }
  return 0;
}

export function widthFor(node: IRNode): number {
  const n = branchCount(node);
  if (n > 0) return Math.max(NODE_W, n * 104 + 24);
  return NODE_W;
}

/** The CG thumbnail on a card, plus its margin. Kept here because the layout
 *  has to reserve the space: a node that renders taller than it was laid out
 *  for is a node that overlaps the one beneath it. Must match `.node-thumb` in
 *  the CSS. */
export const THUMB_H = 96;

export function heightFor(node: IRNode): number {
  const base = ["choice", "hub", "condition"].includes(node.type) ? NODE_H + 40 : NODE_H;
  return node.data?.image_hash ? base + THUMB_H : base;
}
