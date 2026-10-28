import { describe, it, expect } from "vitest";
import { routeDown, turnRanks, type Box, type Point } from "./route";

const box = (id: string, left: number, top: number, w = 240, h = 100): Box =>
  ({ id, left, top, right: left + w, bottom: top + h });

/** Whether any segment of the path passes through a card. */
function crosses(points: Point[], boxes: Box[]): string[] {
  const hit: string[] = [];
  for (let i = 1; i < points.length; i++) {
    const [a, b] = [points[i - 1], points[i]];
    for (const bx of boxes) {
      if (Math.max(a.x, b.x) > bx.left && Math.min(a.x, b.x) < bx.right
        && Math.max(a.y, b.y) > bx.top && Math.min(a.y, b.y) < bx.bottom) {
        hit.push(bx.id);
      }
    }
  }
  return hit;
}

describe("edge routing", () => {
  it("runs straight down when nothing is in the way", () => {
    expect(routeDown({ x: 100, y: 100 }, { x: 100, y: 300 }, [])).toEqual([
      { x: 100, y: 100 }, { x: 100, y: 300 },
    ]);
  });

  it("keeps the turn above the target, clear of the cards beside it", () => {
    const siblings = [box("a", -200, 300), box("b", 200, 300)];
    const path = routeDown({ x: 100, y: 100 }, { x: 320, y: 300 }, siblings, ["b"])!;
    expect(path).toHaveLength(4);
    expect(path[1].y).toBeLessThan(300);
    expect(crosses(path, siblings.filter((s) => s.id !== "b"))).toEqual([]);
  });

  it("goes around a tall branch standing between a port and a merge point", () => {
    // A branch column directly under the port; the merge point far below.
    const branch = [box("b1", 0, 200), box("b2", 0, 360), box("b3", 0, 520)];
    const path = routeDown({ x: 100, y: 120 }, { x: 120, y: 760 }, branch)!;
    expect(crosses(path, branch)).toEqual([]);
  });

  it("finds the gap between two columns when both ends are blocked", () => {
    const cards = [box("l", -300, 200, 240, 400), box("r", 0, 200, 240, 400)];
    // Port above the right column, target below the left one.
    const path = routeDown({ x: 60, y: 120 }, { x: -180, y: 700 }, cards)!;
    expect(crosses(path, cards)).toEqual([]);
    const lane = path.find((p, i) => i > 0 && p.y > 200 && p.y < 600);
    expect(lane?.x ?? path[2].x).toBeGreaterThan(-60);
    expect(lane?.x ?? path[2].x).toBeLessThan(0);
  });

  it("leaves a loop back up the story to the stock curve", () => {
    expect(routeDown({ x: 0, y: 500 }, { x: 0, y: 100 }, [])).toBeNull();
  });

  it("staggers the turns of a fan so neighbours heading the same way don't overlap", () => {
    // Ports 2 and 3 both head right, and port 2's target lies past port 3.
    const ranks = turnRanks([
      { id: "o1", from: 100, to: -200 },
      { id: "o2", from: 200, to: 320 },
      { id: "o3", from: 300, to: 600 },
    ]);
    const from = { o2: { x: 200, y: 100 }, o3: { x: 300, y: 100 } };
    const o2 = routeDown(from.o2, { x: 320, y: 300 }, [], [], ranks.get("o2"))!;
    const o3 = routeDown(from.o3, { x: 600, y: 300 }, [], [], ranks.get("o3"))!;
    // o2 turns below o3, so its horizontal passes under o3's vertical.
    expect(o2[1].y).toBeGreaterThan(o3[1].y);
    expect(ranks.get("o1")).toBe(0);
  });
});
