/**
 * Edge routing for the canvas: square paths that go around cards instead of
 * through them.
 *
 * A stock curve from a branch port to a merge point far below cuts straight
 * across whatever columns sit between the two. These paths run down a clear
 * column instead -- the port's own, the target's, or failing both the nearest
 * gap between cards -- and turn just below the source or just above the
 * target, where there is room to.
 */

export interface Point {
  x: number;
  y: number;
}

export interface Box {
  id: string;
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** How far an edge runs straight out of a port, or into a card, before turning. */
const STUB = 24;
/** Space kept between an edge and a card it passes. */
const CLEAR = 14;
/** Vertical spacing between the turns of edges fanning out the same way. */
const TURN_STEP = 12;

/**
 * Corner points for an edge that leaves the bottom of one card and enters the
 * top of a card below it. Null when the target is not below the source (a
 * loop back up the story, or cards side by side), which has no clear column
 * to run down and keeps the stock curve.
 *
 * `turn` raises the turn above the target by that many steps -- see
 * `turnRanks`.
 */
export function routeDown(
  from: Point, to: Point, boxes: Box[], skip: string[] = [], turn = 0,
): Point[] | null {
  if (to.y - from.y < STUB * 2) return null;
  const high = from.y + STUB;
  const low = Math.max(high, to.y - STUB - turn * TURN_STEP);
  const obstacles = boxes.filter((b) => !skip.includes(b.id));

  const hits = (b: Box, x1: number, x2: number, y1: number, y2: number) =>
    b.left - CLEAR < Math.max(x1, x2) && b.right + CLEAR > Math.min(x1, x2)
    && b.top - CLEAR < Math.max(y1, y2) && b.bottom + CLEAR > Math.min(y1, y2);
  const clear = (points: Point[]) => points.every((p, i) => {
    if (i === 0) return true;
    const q = points[i - 1];
    // The stubs into and out of the cards themselves never count.
    const y1 = i === 1 ? Math.min(high, p.y) : q.y;
    const y2 = i === points.length - 1 ? Math.max(low, q.y) : p.y;
    return !obstacles.some((b) => hits(b, q.x, p.x, y1, y2));
  });

  const candidates: Point[][] = [
    // Down the port's own column, turning just above the target.
    [from, { x: from.x, y: low }, { x: to.x, y: low }, to],
    // Turning just below the source, then down the target's column.
    [from, { x: from.x, y: high }, { x: to.x, y: high }, to],
  ];
  // Down the nearest gap between the cards in the way.
  const ideal = (from.x + to.x) / 2;
  const cost = (x: number) => Math.abs(x - from.x) + Math.abs(x - to.x);
  for (const x of gaps(obstacles, high, low, ideal).sort((a, b) => cost(a) - cost(b))) {
    candidates.push([from, { x: from.x, y: high }, { x, y: high }, { x, y: low }, { x: to.x, y: low }, to]);
  }

  const chosen = candidates.find((points) => clear(simplify(points))) ?? candidates[0];
  return simplify(chosen);
}

/**
 * How many steps above its target each edge of a fan turns, keyed by id.
 *
 * Edges from neighbouring ports that head the same way would otherwise turn
 * at the same height and run along each other: port 2 going right to a
 * column past port 3 draws its horizontal straight over port 3's. Staggered
 * -- the port furthest from where they are heading turning lowest -- each
 * edge's horizontal passes below the verticals of the ports it crosses, and
 * nothing overlaps.
 */
export function turnRanks(fan: { id: string; from: number; to: number }[]): Map<string, number> {
  const ranks = new Map<string, number>();
  const right = fan.filter((e) => e.to - e.from >= 1).sort((a, b) => a.from - b.from);
  const left = fan.filter((e) => e.from - e.to >= 1).sort((a, b) => b.from - a.from);
  for (const group of [right, left]) group.forEach((e, i) => ranks.set(e.id, i));
  return ranks;
}

/** A clear x in each gap between the cards spanning the band from `top` to
 *  `bottom`, plus one either side of them all. */
function gaps(boxes: Box[], top: number, bottom: number, ideal: number): number[] {
  const spans = boxes
    .filter((b) => b.top - CLEAR < bottom && b.bottom + CLEAR > top)
    .map((b) => [b.left - CLEAR, b.right + CLEAR])
    .sort((a, b) => a[0] - b[0]);
  if (!spans.length) return [];
  const merged: number[][] = [];
  for (const span of spans) {
    const last = merged[merged.length - 1];
    if (last && span[0] <= last[1]) last[1] = Math.max(last[1], span[1]);
    else merged.push([...span]);
  }
  const xs = [merged[0][0] - CLEAR, merged[merged.length - 1][1] + CLEAR];
  for (let i = 1; i < merged.length; i++) {
    const [lo, hi] = [merged[i - 1][1], merged[i][0]];
    // A narrow gap is run down the middle; a wide one as near the straight
    // line as it allows.
    xs.push(hi - lo < 4 * CLEAR ? (lo + hi) / 2 : Math.min(hi - CLEAR, Math.max(lo + CLEAR, ideal)));
  }
  return xs;
}

/** Drop repeated points and the middle of any straight run. */
export function simplify(points: Point[]): Point[] {
  const out: Point[] = [];
  for (const p of points) {
    const last = out[out.length - 1];
    if (last && Math.abs(last.x - p.x) < 0.5 && Math.abs(last.y - p.y) < 0.5) continue;
    const before = out[out.length - 2];
    if (before && last
      && ((Math.abs(before.x - last.x) < 0.5 && Math.abs(last.x - p.x) < 0.5)
        || (Math.abs(before.y - last.y) < 0.5 && Math.abs(last.y - p.y) < 0.5))) {
      out[out.length - 1] = p;
      continue;
    }
    out.push(p);
  }
  return out;
}

/** SVG path through the points with rounded corners. */
export function roundedPath(points: Point[], radius = 8): string {
  const dist = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
  const toward = (a: Point, b: Point, r: number) => {
    const d = dist(a, b) || 1;
    return { x: a.x + ((b.x - a.x) * r) / d, y: a.y + ((b.y - a.y) * r) / d };
  };
  let d = `M ${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const [prev, at, next] = [points[i - 1], points[i], points[i + 1]];
    const r = Math.min(radius, dist(prev, at) / 2, dist(at, next) / 2);
    const a = toward(at, prev, r);
    const b = toward(at, next, r);
    d += ` L ${a.x},${a.y} Q ${at.x},${at.y} ${b.x},${b.y}`;
  }
  const last = points[points.length - 1];
  return `${d} L ${last.x},${last.y}`;
}

/** Where the label goes: the middle of the path's longest straight run, so
 *  the labels on a fan of branches spread out along their own segments
 *  instead of piling up where the edges leave the ports. */
export function labelPoint(points: Point[]): Point {
  let best = { x: points[0].x, y: points[0].y };
  let length = -1;
  for (let i = 1; i < points.length; i++) {
    const [a, b] = [points[i - 1], points[i]];
    const run = Math.hypot(a.x - b.x, a.y - b.y);
    if (run > length) {
      length = run;
      best = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    }
  }
  return best;
}
