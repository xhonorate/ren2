import type { IREdge, IRNode } from "./types";

/** An event CG on screen, and the node it belongs to. */
export interface CgRef {
  node: string;
  image: string;
}

export interface CgContext {
  /** The CG still up as this node starts -- a held one from upstream. */
  held: CgRef | null;
  /** The nearest rendered CG above this node in its scene, held or not: what
   *  "start from the previous CG" starts from. */
  previous: CgRef | null;
}

const PATH_LIMIT = 400;

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/**
 * Held-CG context for any node, walked exactly as the compiler walks it.
 *
 * Mirrors `graph.path_to` (first predecessor by source, handle, edge id) and
 * `graph._track_cg` / `graph.stage_clears` on the backend, and has to stay in
 * step with them -- the inspector and the cards say what the build will do.
 * Built once per graph; each lookup is one walk up the path.
 */
export function cgContext(nodes: IRNode[], edges: IREdge[]): (id: string) => CgContext {
  const byId = new Map(nodes.filter((n) => !n.orphaned).map((n) => [n.id, n]));
  const incoming = new Map<string, IREdge[]>();
  for (const edge of edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target)) continue;
    const list = incoming.get(edge.target) ?? [];
    list.push(edge);
    incoming.set(edge.target, list);
  }
  for (const list of incoming.values()) {
    list.sort((a, b) => cmp(a.source, b.source) || cmp(a.source_handle, b.source_handle)
      || cmp(a.id, b.id));
  }

  const pathTo = (id: string): IRNode[] => {
    const chain: IRNode[] = [];
    const seen = new Set<string>();
    let current: string | undefined = id;
    while (current && byId.has(current) && !seen.has(current) && chain.length < PATH_LIMIT) {
      seen.add(current);
      chain.push(byId.get(current)!);
      current = incoming.get(current)?.[0]?.source;
    }
    return chain.reverse();
  };

  return (id: string): CgContext => {
    const path = pathTo(id);
    const held = heldAlong(path)[path.length - 1] ?? null;
    const upstream = path.slice(0, -1);

    let previous: CgRef | null = null;
    for (let i = upstream.length - 1; i >= 0; i -= 1) {
      const node = upstream[i];
      if (node.type === "scene") break;
      const d = node.data ?? {};
      if ((node.type === "say" || node.type === "narration") && d.cg && d.image_hash) {
        previous = { node: node.id, image: d.image_hash };
        break;
      }
    }
    return { held, previous };
  };
}

/**
 * The held CG as each node of `sequence` starts, playing the nodes in order.
 *
 * `cgContext` feeds it the compiler's path; the Script view feeds it its own
 * rows, which follow the branches picked there rather than the first
 * predecessor. Mirrors `graph._track_cg`: a line flagged for a CG takes the
 * slot whether or not it is rendered, so rendering it never moves where a
 * run ends.
 */
export function heldAlong(sequence: IRNode[]): (CgRef | null)[] {
  const out: (CgRef | null)[] = [];
  let held: CgRef | null = null;
  let location: string | null = null;
  for (const node of sequence) {
    out.push(held);
    const d = node.data ?? {};
    if (node.type === "scene") {
      held = null;
      location = d.background || location;
    } else if (node.type === "stage") {
      if (stageClears(d, location)) held = null;
      location = d.location || location;
    } else if (node.type === "say" || node.type === "narration") {
      if (d.cg) {
        if (!d.cg_hold) held = null;
        else if (d.image_hash) held = { node: node.id, image: d.image_hash };
      } else if (d.cg_clear) {
        held = null;
      }
    }
  }
  return out;
}

/** `graph.stage_clears`: a Stage node that compiles to `scene`. */
export function stageClears(d: Record<string, any>, location: string | null): boolean {
  return Boolean(d.reset || (d.location && d.location !== location));
}
