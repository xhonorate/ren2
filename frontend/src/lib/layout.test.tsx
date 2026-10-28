import { describe, it, expect } from "vitest";
import { heightFor, layoutGraph, widthFor } from "./layout";

const mk = (id: string, type: string, data: any = {}): any => ({
  id, type, data, x: 0, y: 0, parent_id: null, pinned_label: false, slug: id,
  notes: "", text_hash: "", text_status: "none", image_status: "none",
  audio_status: "none", variants: [], active_variant: null, orphaned: false,
});

const byId = (placed: { id: string; x: number; y: number }[]) =>
  Object.fromEntries(placed.map((p) => [p.id, p]));

/** Every pair of cards whose rectangles intersect. */
function overlaps(nodes: any[], placed: { id: string; x: number; y: number }[]) {
  const at = byId(placed);
  const boxes = nodes.map((n) => ({
    id: n.id, left: at[n.id].x, top: at[n.id].y,
    right: at[n.id].x + widthFor(n), bottom: at[n.id].y + heightFor(n),
  }));
  const hits: string[] = [];
  boxes.forEach((a, i) => boxes.slice(i + 1).forEach((b) => {
    if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) {
      hits.push(`${a.id}/${b.id}`);
    }
  }));
  return hits;
}

describe("auto-layout branch ordering", () => {
  it("places branches left-to-right in option order", () => {
    const choice = mk("q", "choice", {
      options: [
        { id: "o1", label: "First" },
        { id: "o2", label: "Second" },
        { id: "o3", label: "Third" },
      ],
    });
    const nodes = [
      mk("s", "scene"), choice,
      mk("a", "say"), mk("b", "say"), mk("c", "say"),
      mk("a2", "say"), mk("b2", "say"), mk("c2", "say"),
    ];
    const edges = [
      { source: "s", target: "q", source_handle: "out" },
      { source: "q", target: "a", source_handle: "o1" },
      { source: "q", target: "b", source_handle: "o2" },
      { source: "q", target: "c", source_handle: "o3" },
      { source: "a", target: "a2", source_handle: "out" },
      { source: "b", target: "b2", source_handle: "out" },
      { source: "c", target: "c2", source_handle: "out" },
    ];

    const x = Object.fromEntries(
      layoutGraph(nodes, edges).map((p) => [p.id, p.x]),
    );

    expect(x.a).toBeLessThan(x.b);
    expect(x.b).toBeLessThan(x.c);
    // Each branch's subtree travels with it.
    expect(x.a2).toBeLessThan(x.b2);
    expect(x.b2).toBeLessThan(x.c2);
  });

  it("orders branches by option, not by edge order", () => {
    // Option order deliberately the reverse of the edge declaration order,
    // so a layout that just follows insertion order fails this.
    const choice = mk("q", "choice", {
      options: [{ id: "o3", label: "Third" }, { id: "o2", label: "Second" }, { id: "o1", label: "First" }],
    });
    const nodes = [mk("s", "scene"), choice, mk("a", "say"), mk("b", "say"), mk("c", "say")];
    const edges = [
      { source: "s", target: "q", source_handle: "out" },
      { source: "q", target: "a", source_handle: "o1" },
      { source: "q", target: "b", source_handle: "o2" },
      { source: "q", target: "c", source_handle: "o3" },
    ];

    const x = Object.fromEntries(
      layoutGraph(nodes, edges).map((p) => [p.id, p.x]),
    );

    // o3 -> c is listed first, so c is leftmost.
    expect(x.c).toBeLessThan(x.b);
    expect(x.b).toBeLessThan(x.a);
  });

  it("centres a shared merge point under the choice", () => {
    const choice = mk("q", "choice", {
      options: [{ id: "o1", label: "A" }, { id: "o2", label: "B" }],
    });
    const nodes = [mk("s", "scene"), choice, mk("a", "say"), mk("b", "say"), mk("m", "say")];
    const edges = [
      { source: "s", target: "q", source_handle: "out" },
      { source: "q", target: "a", source_handle: "o1" },
      { source: "q", target: "b", source_handle: "o2" },
      { source: "a", target: "m", source_handle: "out" },
      { source: "b", target: "m", source_handle: "out" },
    ];

    const at = byId(layoutGraph(nodes, edges));

    expect(at.a.x).toBeLessThan(at.b.x);
    // The merge node belongs to neither branch: it sits centred under the
    // choice, below both of them.
    expect(at.m.x + 120).toBe(at.q.x + widthFor(choice) / 2);
    expect(at.m.y).toBeGreaterThan(at.a.y);
    expect(at.m.y).toBeGreaterThan(at.b.y);
  });

  it("is a no-op for a choice whose options all converge on one node", () => {
    const choice = mk("q", "choice", {
      options: [{ id: "o1", label: "A" }, { id: "o2", label: "B" }],
    });
    const nodes = [mk("s", "scene"), choice, mk("m", "say")];
    const edges = [
      { source: "s", target: "q", source_handle: "out" },
      { source: "q", target: "m", source_handle: "o1" },
      { source: "q", target: "m", source_handle: "o2" },
    ];
    expect(() => layoutGraph(nodes, edges)).not.toThrow();
  });
  it("keeps a nested choice inside its own branch's column", () => {
    // Branch A splits again; branch B is a long run. A's sub-branches must
    // stay left of B rather than spilling into its column.
    const q = mk("q", "choice", { options: [{ id: "o1", label: "A" }, { id: "o2", label: "B" }] });
    const qa = mk("qa", "choice", {
      options: [{ id: "o1", label: "A1" }, { id: "o2", label: "A2" }, { id: "o3", label: "A3" }],
    });
    const nodes = [
      mk("s", "scene"), q, mk("a", "say"), qa,
      mk("a1", "say"), mk("a2", "say"), mk("a3", "say"), mk("a2b", "say"),
      mk("b", "say"), mk("b2", "say"), mk("b3", "say"), mk("b4", "say"), mk("b5", "say"),
    ];
    const edges = [
      { source: "s", target: "q", source_handle: "out" },
      { source: "q", target: "a", source_handle: "o1" },
      { source: "q", target: "b", source_handle: "o2" },
      { source: "a", target: "qa", source_handle: "out" },
      { source: "qa", target: "a1", source_handle: "o1" },
      { source: "qa", target: "a2", source_handle: "o2" },
      { source: "qa", target: "a3", source_handle: "o3" },
      { source: "a2", target: "a2b", source_handle: "out" },
      { source: "b", target: "b2", source_handle: "out" },
      { source: "b2", target: "b3", source_handle: "out" },
      { source: "b3", target: "b4", source_handle: "out" },
      { source: "b4", target: "b5", source_handle: "out" },
    ];

    const placed = layoutGraph(nodes, edges);
    const at = byId(placed);

    expect(overlaps(nodes, placed)).toEqual([]);
    expect(at.a1.x).toBeLessThan(at.a2.x);
    expect(at.a2.x).toBeLessThan(at.a3.x);
    // The whole of branch A, sub-branches included, is left of branch B.
    for (const id of ["a", "qa", "a1", "a2", "a3", "a2b"]) {
      expect(at[id].x + 240).toBeLessThan(at.b.x);
    }
  });

  it("gives a port that skips ahead to the merge point a lane of its own", () => {
    // o1 and o3 run straight to the merge point; only o2 has content. The
    // content stays under o2 -- the middle -- with room either side for the
    // other two edges, instead of sitting where those edges are drawn.
    const q = mk("q", "choice", {
      options: [{ id: "o1", label: "A" }, { id: "o2", label: "B" }, { id: "o3", label: "C" }],
    });
    const nodes = [mk("s", "scene"), q, mk("b", "say"), mk("m", "say")];
    const edges = [
      { source: "s", target: "q", source_handle: "out" },
      { source: "q", target: "m", source_handle: "o1" },
      { source: "q", target: "b", source_handle: "o2" },
      { source: "q", target: "m", source_handle: "o3" },
      { source: "b", target: "m", source_handle: "out" },
    ];
    const at = byId(layoutGraph(nodes, edges));
    expect(at.b.x + 120).toBe(at.q.x + widthFor(q) / 2);
    expect(at.m.y).toBeGreaterThan(at.b.y);
  });

  it("lays out a loop and places every node", () => {
    const hub = mk("h", "hub", { options: [{ id: "t1", label: "One" }, { id: "t2", label: "Two" }] });
    const nodes = [mk("s", "scene"), hub, mk("t1", "say"), mk("t2", "say"), mk("x", "say"), mk("y", "say")];
    const edges = [
      { source: "s", target: "h", source_handle: "out" },
      { source: "h", target: "t1", source_handle: "t1" },
      { source: "h", target: "t2", source_handle: "t2" },
      { source: "t1", target: "h", source_handle: "out" },
      { source: "t2", target: "h", source_handle: "out" },
      // An island that is nothing but a loop.
      { source: "x", target: "y", source_handle: "out" },
      { source: "y", target: "x", source_handle: "out" },
    ];
    const placed = layoutGraph(nodes, edges);
    expect(placed.map((p) => p.id).sort()).toEqual(nodes.map((n) => n.id).sort());
    expect(overlaps(nodes, placed)).toEqual([]);
    const at = byId(placed);
    expect(at.t1.x).toBeLessThan(at.t2.x);
    expect(at.t1.y).toBeGreaterThan(at.h.y);
  });

  it("makes room for a card that renders taller than the estimate", () => {
    const nodes = [mk("s", "scene"), mk("a", "say"), mk("b", "say")];
    const edges = [
      { source: "s", target: "a", source_handle: "out" },
      { source: "a", target: "b", source_handle: "out" },
    ];
    const at = byId(layoutGraph(nodes, edges, { a: { width: 240, height: 300 } }));
    expect(at.b.y).toBeGreaterThanOrEqual(at.a.y + 300);
  });

  it("never overlaps cards, whatever the branching", () => {
    // Deterministic pseudo-random story graphs: runs, choices of 2-4 options,
    // conditions, branches that merge and branches that loop back.
    let seed = 7;
    const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
    for (let round = 0; round < 40; round++) {
      const nodes: any[] = [mk("s", "scene")];
      const edges: { source: string; target: string; source_handle: string }[] = [];
      const open: { id: string; handle: string }[] = [{ id: "s", handle: "out" }];
      let count = 0;
      while (open.length && count < 60) {
        const at = open.splice(Math.floor(rand() * open.length), 1)[0];
        const id = `n${count++}`;
        const roll = rand();
        let node: any;
        if (roll < 0.2) {
          const n = 2 + Math.floor(rand() * 3);
          node = mk(id, "choice", {
            options: Array.from({ length: n }, (_, i) => ({ id: `o${i}`, label: `${i}` })),
          });
          node.data.options.forEach((o: any) => open.push({ id, handle: o.id }));
        } else if (roll < 0.28) {
          node = mk(id, "condition", { branches: [{ id: "b1", expr: "x" }], has_else: true });
          open.push({ id, handle: "b1" }, { id, handle: "else" });
        } else {
          node = mk(id, "say", rand() < 0.1 ? { image_hash: "h" } : {});
          if (rand() < 0.85) open.push({ id, handle: "out" });
        }
        nodes.push(node);
        edges.push({ source: at.id, target: id, source_handle: at.handle });
        // Now and then a second way in: a merge, or a loop back up.
        if (count > 3 && rand() < 0.2) {
          const from = nodes[Math.floor(rand() * nodes.length)];
          edges.push({ source: from.id, target: id, source_handle: "out" });
        }
      }
      const placed = layoutGraph(nodes, edges);
      expect(placed).toHaveLength(nodes.length);
      expect(overlaps(nodes, placed)).toEqual([]);
    }
  });
});
