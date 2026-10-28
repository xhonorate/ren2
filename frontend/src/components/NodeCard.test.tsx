import { describe, it, expect } from "vitest";
import { portsFor } from "./NodeCard";
import { branchCount, widthFor } from "../lib/layout";

const mk = (id: string, type: string, data: any = {}): any => ({
  id, type, data, x: 0, y: 0, parent_id: null, pinned_label: false, slug: id,
  notes: "", text_hash: "", text_status: "none", image_status: "none",
  audio_status: "none", variants: [], active_variant: null, orphaned: false,
});

describe("hub ports", () => {
  it("gives every wired topic a port", () => {
    const hub = mk("h", "hub", {
      options: [
        { id: "o1", label: "The club" },
        { id: "o2", label: "Her sister" },
      ],
    });
    expect(portsFor(hub).map((p) => p.id)).toEqual(["o1", "o2"]);
    expect(portsFor(hub)[1].name).toBe("Her sister");
  });

  it("gives a route-targeted topic no port to wire", () => {
    // Its destination is resolved at play time, so there is nothing to drag
    // an edge to -- showing a port would invite wiring one that never fires.
    const hub = mk("h", "hub", {
      options: [
        { id: "o1", label: "Talk" },
        { id: "o2", label: "See Aiko", target_route: "aiko" },
      ],
    });
    expect(portsFor(hub).map((p) => p.id)).toEqual(["o1"]);
  });

  it("grows sideways with its topics, like a choice", () => {
    const hub = mk("h", "hub", {
      options: [{ id: "o1" }, { id: "o2" }, { id: "o3" }],
    });
    expect(branchCount(hub)).toBe(3);
    expect(widthFor(hub)).toBeGreaterThan(widthFor(mk("s", "say")));
  });
});
