import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RoutesView from "./RoutesView";
import { useStore } from "../lib/store";
import { api } from "../lib/api";

/**
 * The two things this view has to do beyond rendering numbers:
 *
 * 1. A scene row is a link into the scene, in the view that suits what you are
 *    about to do to it. A route list you cannot act from is a report.
 * 2. A scene created here carries the route tag on the way in. An untagged
 *    scene is invisible to every hub that dispatches over the route, and
 *    nothing complains, because claiming no route is legal.
 */

const scene = (over: Partial<any> = {}) => ({
  id: "s1", title: "Rooftop", route: "aiko", nsfw: false,
  words: 120, lines: 10, say_lines: 8, narration_lines: 2, choices: 1,
  options: 2, nodes: 12, voiced: 4, voiceable: 8, cgs: 0, cgs_done: 0,
  backgrounds: 1, backgrounds_done: 1, depth: 1,
  speakers: [{ id: "aiko", name: "Aiko", color: "#f00", lines: 8, words: 120 }],
  seconds: 45, voiced_pct: 50, imaged_pct: 100, words_per_line: 12,
  lines_per_choice: 10, ...over,
});

const route = (over: Partial<any> = {}) => ({
  name: "aiko", scenes: 1, scenes_list: [scene()],
  cast: [{ id: "aiko", name: "Aiko", color: "#f00", lines: 8, words: 120, share_pct: 100 }],
  words: 120, lines: 10, say_lines: 8, narration_lines: 2, choices: 1,
  options: 2, nodes: 12, voiced: 4, voiceable: 8, cgs: 0, cgs_done: 0,
  backgrounds: 1, backgrounds_done: 1, max_depth: 1, seconds: 45,
  voiced_pct: 50, imaged_pct: 100, words_per_line: 12, lines_per_choice: 10,
  ...over,
});

const REPORT = {
  words_per_minute: 160,
  routes: [
    route(),
    route({
      name: "diana", words: 40, seconds: 15, lines: 4, choices: 0,
      lines_per_choice: null, voiced_pct: null, voiceable: 0, voiced: 0,
      cast: [],
      scenes_list: [scene({ id: "s2", title: "Library", route: "diana", words: 40 })],
    }),
  ],
  totals: route({ name: "", scenes: 2, words: 160, seconds: 60 }),
  characters: [{
    id: "aiko", name: "Aiko", color: "#f00", lines: 8, words: 120,
    is_player: false, scenes: 1, routes: ["aiko"], voiced: 4, voiceable: 8,
    voiced_pct: 50, share_pct: 75,
  }],
};

beforeEach(() => {
  useStore.setState({
    project: { id: "p1", name: "Test" } as any,
    nodes: [
      { id: "s1", type: "scene", data: { route: "aiko" }, x: 40, y: 100 },
      { id: "sX", type: "scene", data: { route: "aiko" }, x: 40, y: 300 },
    ] as any,
    view: "routes",
  });
  vi.spyOn(api, "pacing").mockResolvedValue(REPORT as any);
});

afterEach(() => vi.restoreAllMocks());

describe("RoutesView", () => {
  it("groups scenes under the route they claim, with the imbalance in view", async () => {
    render(<RoutesView />);
    // "aiko" is also the route listed against the character below, so anchor
    // on the one string that only the route card carries.
    await screen.findByText("＋ New scene in aiko");

    expect(screen.getByText("diana")).toBeTruthy();
    expect(screen.getByText("Rooftop")).toBeTruthy();
    expect(screen.getByText("Library")).toBeTruthy();
    // Diana's route is a third of Aiko's, and the word counts say so.
    expect(screen.getByText("120 words")).toBeTruthy();
    expect(screen.getByText("40 words")).toBeTruthy();
  });

  it("opens the scene on the canvas when its title is clicked", async () => {
    const select = vi.fn();
    const setView = vi.fn();
    useStore.setState({ select, setView } as any);

    render(<RoutesView />);
    fireEvent.click(await screen.findByText("Rooftop"));

    expect(select).toHaveBeenCalledWith("s1");
    expect(setView).toHaveBeenCalledWith("canvas");
    // The shared active-scene key, so Canvas and Script both land on it.
    expect(localStorage.getItem("vn:activeScene:p1")).toBe("s1");
  });

  it("sends you to the Script view when that is the work you meant", async () => {
    const setView = vi.fn();
    useStore.setState({ setView } as any);

    render(<RoutesView />);
    const rows = await screen.findAllByText("script");
    fireEvent.click(rows[0]);

    expect(setView).toHaveBeenCalledWith("script");
  });

  it("tags a new scene with the route it was added under", async () => {
    const addNode = vi.fn().mockResolvedValue({ id: "new" });
    useStore.setState({ addNode, notify: vi.fn() } as any);

    render(<RoutesView />);
    fireEvent.click(await screen.findByText("＋ New scene in aiko"));
    fireEvent.change(screen.getByPlaceholderText("Scene title"),
                     { target: { value: "Confession" } });
    fireEvent.click(screen.getByText("Add"));

    await waitFor(() => expect(addNode).toHaveBeenCalled());
    const [type, data, opts] = addNode.mock.calls[0];
    expect(type).toBe("scene");
    expect(data).toEqual({ title: "Confession", route: "aiko" });
    // Below the route's last scene, because canvas order is route order --
    // here and in the compiled Ren'Py route table.
    expect(opts.y).toBe(520);
  });

  it("renaming a route carries the hubs that dispatch over it", async () => {
    const rename = vi.spyOn(api, "renameRoute").mockResolvedValue({ updated: 3 });
    const notify = vi.fn();
    useStore.setState({ notify, refreshGraph: vi.fn() } as any);

    render(<RoutesView />);
    const buttons = await screen.findAllByText("rename");
    fireEvent.click(buttons[0]);
    fireEvent.change(screen.getByDisplayValue("aiko"),
                     { target: { value: "aiko_true" } });
    fireEvent.click(screen.getByText("Rename"));

    await waitFor(() => expect(rename).toHaveBeenCalledWith("p1", "aiko", "aiko_true"));
    // The count is the only sign the hubs moved with the scenes.
    expect(notify).toHaveBeenCalledWith("aiko → aiko_true (3 nodes updated)");
  });
});
