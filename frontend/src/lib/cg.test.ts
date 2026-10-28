import { describe, expect, it } from "vitest";
import { cgContext } from "./cg";
import type { IREdge, IRNode } from "./types";

const node = (id: string, type: string, data: Record<string, any> = {}): IRNode =>
  ({ id, type, data, orphaned: false } as unknown as IRNode);

const chain = (...ids: string[]): IREdge[] =>
  ids.slice(1).map((id, i) => ({
    id: `${ids[i]}->${id}`, source: ids[i], target: id, source_handle: "out", label: "",
  }));

const held = { cg: true, cg_hold: true };

// The same cases as test_path_state_ends_a_held_cg_wherever_the_build_clears_the_stage
// in backend/tests/test_compiler.py -- this is the browser twin of that rule.
describe("cgContext", () => {
  const nodes = [
    node("s1", "scene", { background: "roof" }),
    node("a", "narration", { ...held, image_hash: "h1" }),
    node("same", "stage", { location: "roof" }),
    node("b", "narration"),
    node("moved", "stage", { location: "classroom" }),
    node("c", "narration", { ...held, image_hash: "h2" }),
    node("clear", "narration", { cg_clear: true }),
    node("d", "narration"),
    node("s2", "scene"),
    node("e", "narration"),
  ];
  const at = cgContext(nodes, chain(...nodes.map((n) => n.id)));

  it("holds a CG over the lines after it, until the stage really moves", () => {
    expect(at("a").held).toBeNull();
    expect(at("same").held).toEqual({ node: "a", image: "h1" });
    expect(at("b").held).toEqual({ node: "a", image: "h1" });
    expect(at("moved").held).toEqual({ node: "a", image: "h1" });
    expect(at("c").held).toBeNull();
    expect(at("clear").held).toEqual({ node: "c", image: "h2" });
    expect(at("d").held).toBeNull();
  });

  it("finds the previous CG whether or not it is still up, within the scene", () => {
    expect(at("b").previous).toEqual({ node: "a", image: "h1" });
    expect(at("d").previous).toEqual({ node: "c", image: "h2" });
    expect(at("a").previous).toBeNull();
    expect(at("e").previous).toBeNull();
  });

  it("lets an unrendered CG end a run, and a held one stand the last picture in", () => {
    const lines = [
      node("a", "narration", { ...held, image_hash: "h1" }),
      node("b", "narration", { ...held }),
      node("c", "narration", { cg: true }),
      node("d", "narration"),
    ];
    const ctx = cgContext(lines, chain("a", "b", "c", "d"));
    expect(ctx("c").held).toEqual({ node: "a", image: "h1" });
    expect(ctx("d").held).toBeNull();
  });

  it("walks the same predecessor as the backend at a merge", () => {
    const nodes2 = [
      node("x", "narration", { ...held, image_hash: "hx" }),
      node("y", "narration", { cg: true, image_hash: "hy" }),
      node("m", "narration"),
    ];
    const edges: IREdge[] = [
      { id: "2", source: "y", target: "m", source_handle: "out", label: "" },
      { id: "1", source: "x", target: "m", source_handle: "out", label: "" },
    ];
    // Sorted by source id: "x" comes first, and x holds.
    expect(cgContext(nodes2, edges)("m").held).toEqual({ node: "x", image: "hx" });
  });
});
