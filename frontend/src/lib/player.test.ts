import { describe, expect, it } from "vitest";
import { EMPTY_STAGE, FROM_NODE, nearestSprite, run, type Stage } from "./player";
import type { PreviewOp, PreviewProgram } from "./types";

const program = (blocks: Record<string, PreviewOp[]>, entry = "start"): PreviewProgram => ({
  entry,
  blocks,
  labels: {},
  characters: {
    aiko: { id: "aiko", name: "Aiko", color: "#fff", is_player: false, voiced: true },
    mio: { id: "mio", name: "Mio", color: "#fff", is_player: false, voiced: true },
  },
  locations: {},
  flags: {},
  routes: {},
  warnings: [],
  scenes: [],
});

const show = (node: string, character: string, expression = "neutral", extra = {}): PreviewOp => ({
  op: "show", node, character, name: character, outfit: "uniform", pose: "front",
  expression, position: "center", sprite: null, ...extra,
});

const frames = (p: PreviewProgram, label = p.entry!) => [{ ops: p.blocks[label], index: 0, label }];

describe("run", () => {
  it("stops at a line with the stage as it stood before that node", () => {
    const p = program({
      start: [
        { op: "scene", node: "s1", background: "roof", time_of_day: "day" },
        show("st", "aiko"),
        show("a", "aiko", "smiling"),
        { op: "say", node: "a", speaker: "aiko", text: "Hi." },
      ],
    });
    const out = run(p, { frames: frames(p), flags: {}, stage: EMPTY_STAGE, visited: new Set() });

    expect(out.stop.kind).toBe("say");
    expect(out.stage.sprites.map((s) => [s.expression, s.node])).toEqual([["smiling", "a"]]);
    // The node's own show op had not run yet.
    expect(out.before.sprites.map((s) => [s.expression, s.node])).toEqual([["neutral", "st"]]);
    expect(out.before.background).toEqual({ id: "roof", time: "day", node: "s1" });
  });

  it("names the upstream source for restored setup, and never marks it", () => {
    const p = program({
      [FROM_NODE]: [
        { op: "scene", node: "b", background: "roof", restored: true, source: "s1" },
        show("b", "aiko", "neutral", { restored: true, source: "st" }),
        { op: "say", node: "b", speaker: "aiko", text: "Two." },
      ],
    }, FROM_NODE);
    const out = run(p, { frames: frames(p), flags: {}, stage: EMPTY_STAGE, visited: new Set() });

    expect(out.stage.background.node).toBe("s1");
    expect(out.stage.sprites[0].node).toBe("st");
    // Setup counts as what was already on stage when the line began.
    expect(out.before.sprites.map((s) => s.character)).toEqual(["aiko"]);
  });

  it("re-runs an edited line against the stage this playthrough built", () => {
    // Played: Mio came on in a branch the canonical path does not take.
    const played: Stage = {
      background: { id: "roof", time: "dusk", node: "s1" },
      sprites: [
        { character: "mio", name: "Mio", outfit: "uniform", pose: "front", expression: "neutral",
          position: "left", sprite: null, node: "branch" },
        { character: "aiko", name: "Aiko", outfit: "uniform", pose: "front",
          expression: "neutral", position: "right", sprite: null, node: "st" },
      ],
      cg: null,
    };
    // Recompiled after the expression edit; its setup only knows Aiko.
    const block: PreviewOp[] = [
      { op: "scene", node: "a", background: "roof", restored: true, source: "s1" },
      show("a", "aiko", "neutral", { restored: true, source: "st" }),
      show("a", "aiko", "angry"),
      { op: "say", node: "a", speaker: "aiko", text: "Hi." },
    ];
    const start = block.findIndex((op) => !op.restored);
    const p = program({ [FROM_NODE]: block }, FROM_NODE);
    const out = run(p, {
      frames: [{ ops: block, index: start, label: "start" }],
      flags: { met: true }, stage: played, visited: new Set(["start"]),
    });

    expect(out.stop.kind).toBe("say");
    expect(out.flags).toEqual({ met: true });
    expect(out.stage.background.time).toBe("dusk");
    expect(out.stage.sprites.map((s) => [s.character, s.expression])).toEqual(
      [["mio", "neutral"], ["aiko", "angry"]]);
  });

  it("keeps a held CG up across lines until something takes it down", () => {
    const cg = (node: string, image: string | null, extra = {}): PreviewOp =>
      ({ op: "cg", node, image, transition: "none", ...extra });
    const p = program({
      start: [
        { op: "scene", node: "s1", background: "roof" },
        cg("a", "h1"), { op: "say", node: "a", text: "Held." },
        { op: "say", node: "b", text: "Still up." },
        // A Stage node that stays put compiles to no `scene` in the build.
        { op: "scene", node: "st", background: "roof", keep_sprites: true, keep_cg: true },
        { op: "say", node: "c", text: "Still up after staging." },
        cg("d", null, { transition: "dissolve" }), { op: "say", node: "d", text: "Cleared." },
        cg("e", "h2"), { op: "say", node: "e", text: "Another." },
        { op: "scene", node: "s2", background: "street" },
        { op: "say", node: "f", text: "New scene." },
      ],
    });
    const seen: [string, string | null, string | null][] = [];
    let input = { frames: frames(p), flags: {}, stage: EMPTY_STAGE, visited: new Set<string>() };
    for (let i = 0; i < 6; i += 1) {
      const out = run(p, input);
      if (out.stop.kind !== "say") throw new Error(out.stop.kind);
      seen.push([out.stop.op.node, out.stage.cg?.image ?? null, out.stage.cg?.node ?? null]);
      input = { frames: out.frames, flags: out.flags, stage: out.stage, visited: out.seen };
    }
    expect(seen).toEqual([
      ["a", "h1", "a"], ["b", "h1", "a"], ["c", "h1", "a"],
      ["d", null, null], ["e", "h2", "e"], ["f", null, null],
    ]);
  });

  it("credits a restored CG to the line that owns it, and re-runs over it", () => {
    const block: PreviewOp[] = [
      { op: "scene", node: "b", background: "roof", restored: true, source: "s1" },
      { op: "cg", node: "b", image: "h1", transition: "none", restored: true, source: "a" },
      { op: "say", node: "b", text: "Held." },
    ];
    const p = program({ [FROM_NODE]: block }, FROM_NODE);
    const out = run(p, { frames: frames(p), flags: {}, stage: EMPTY_STAGE, visited: new Set() });
    expect(out.stage.cg).toEqual({ node: "a", image: "h1" });
    // An edit re-runs from `before`, which already has the held CG in it.
    expect(out.before.cg).toEqual({ node: "a", image: "h1" });
  });

  it("does not mutate the frames it was given", () => {
    const p = program({ start: [{ op: "say", node: "a", text: "One." }] });
    const input = frames(p);
    run(p, { frames: input, flags: {}, stage: EMPTY_STAGE, visited: new Set() });
    expect(input[0].index).toBe(0);
  });
});

describe("nearestSprite", () => {
  // The same fallback order as `_nearest_sprite` in compiler/preview.py.
  it("gives up pose before expression, and never the outfit", () => {
    const sheet = {
      "uniform/front/smiling": "h1",
      "uniform/arms_crossed/neutral": "h2",
      "casual/arms_crossed/smiling": "h3",
    };
    expect(nearestSprite(sheet, "uniform", "arms_crossed", "smiling"))
      .toEqual({ slot: "uniform/front/smiling", hash: "h1" });
    expect(nearestSprite(sheet, "uniform", "arms_crossed", "angry"))
      .toEqual({ slot: "uniform/arms_crossed/neutral", hash: "h2" });
    expect(nearestSprite(sheet, "swimsuit", "front", "smiling")).toBeNull();
  });

  it("falls back to pre-pose keys, then any slot in the outfit", () => {
    expect(nearestSprite({ "uniform/smiling": "old" }, "uniform", "front", "smiling"))
      .toEqual({ slot: "uniform/smiling", hash: "old" });
    expect(nearestSprite({ "uniform/side/crying": "x", "uniform/back/angry": "y" },
                         "uniform", "front", "smiling"))
      .toEqual({ slot: "uniform/back/angry", hash: "y" });
  });
});
