/**
 * The Roleplay view: picking somebody to talk to, starting a scene, sending a
 * turn, and what an empty composer means.
 *
 * The api module is spied per method rather than mocked wholesale, and the store
 * is driven with `setState`, following RoutesView.test.tsx.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import RoleplayView from "./RoleplayView";
import { api } from "../lib/api";
import { useStore } from "../lib/store";

const CHARACTERS = [
  { id: "alice", name: "Alice", color: "#c8c8ff", profile_image: null,
    summary: "Blunt.", voiced: false, conversations: 1, last_at: null },
  { id: "bea", name: "Bea", color: "#ffc8c8", profile_image: null,
    summary: "Careful.", voiced: true, conversations: 0, last_at: null },
];

const conv = (over: Partial<any> = {}) => ({
  id: "rc1", project_id: "p1", character_id: "alice", cast: [],
  title: "First dinner", untitled: true, scene_context: "First dinner at her flat.",
  summary: "", summary_edited: false, state: "open", hidden: false, sort_key: 1,
  location_id: null, time_of_day: null, nsfw: false,
  created_at: "2026-03-02T19:00:00+00:00", updated_at: null, message_count: 2,
  ...over,
});

const message = (over: Partial<any> = {}) => ({
  id: "m1", conv_id: "rc1", seq: 0, role: "cast", text: "", takes: [],
  active_take: null, voice: [], created_at: null, ...over,
});

beforeEach(() => {
  localStorage.clear();
  useStore.setState({
    project: { id: "p1", name: "Test" } as any,
    jobs: [], streams: {}, view: "roleplay",
    notify: vi.fn(), fail: vi.fn(), refreshJobs: vi.fn(), upsertJob: vi.fn(),
  } as any);
  vi.spyOn(api, "rpCharacters").mockResolvedValue({
    characters: CHARACTERS as any,
    persona: { name: "Sam", description: "", notes: "" },
    player: { id: "me", name: "Sam" },
  });
  vi.spyOn(api, "rpConversations").mockResolvedValue({ conversations: [conv()] as any });
  vi.spyOn(api, "rpThread").mockResolvedValue({
    ...conv(),
    messages: [
      message({ id: "m1", role: "cast",
                text: 'Alice (smirking, amused, 0.6): "You\'re early."' }),
      message({ id: "m2", role: "player", seq: 1, text: "Fine. *sits down*" }),
    ],
  } as any);
});

afterEach(() => vi.restoreAllMocks());

describe("RoleplayView", () => {
  it("lists the cast with their scene counts", async () => {
    render(<RoleplayView />);
    expect((await screen.findAllByText("Alice")).length).toBeGreaterThan(0);
    expect(await screen.findByText("Bea")).toBeTruthy();
    expect(await screen.findByText(/1 scene/)).toBeTruthy();
    expect(await screen.findByText(/no scenes/)).toBeTruthy();
  });

  it("shows the persona name on the composer, resolved from the player character", async () => {
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    expect(await screen.findByPlaceholderText("Say something as Sam…")).toBeTruthy();
  });

  it("renders a reply's dialogue with its feeling, and hides the bracket", async () => {
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    expect(await screen.findByText("You're early.")).toBeTruthy();
    // The chip carries the feeling; the raw notation never reaches the screen.
    expect(await screen.findByText("amused")).toBeTruthy();
    expect(screen.queryByText(/smirking, amused, 0.6/)).toBeNull();
  });

  it("shows a player action as an action, not as something said", async () => {
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    expect(await screen.findByText("*sits down*")).toBeTruthy();
  });

  it("offers 'Carry on' for an empty composer and sends it as an empty turn", async () => {
    const turn = vi.spyOn(api, "rpTurn").mockResolvedValue(
      { job_id: "j1", message: null } as any);
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));

    // Empty means "carry on without me" -- an explicit move, so the button
    // stays live and says which of the two it is about to do. It is an icon
    // now, so the label is the accessible name rather than the text.
    fireEvent.click(await screen.findByLabelText("Carry on without me"));
    await waitFor(() => expect(turn).toHaveBeenCalledWith("p1", "rc1", ""));
  });

  it("switches the send button to Send once something is typed", async () => {
    const turn = vi.spyOn(api, "rpTurn").mockResolvedValue(
      { job_id: "j1", message: null } as any);
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    fireEvent.change(await screen.findByPlaceholderText("Say something as Sam…"),
                     { target: { value: "I said I would." } });
    fireEvent.click(await screen.findByLabelText("Send"));
    await waitFor(() =>
      expect(turn).toHaveBeenCalledWith("p1", "rc1", "I said I would."));
  });

  it("starts a scene, then has the cast open it with an empty turn", async () => {
    const create = vi.spyOn(api, "rpCreateConversation").mockResolvedValue(
      conv({ id: "rc2" }) as any);
    const turn = vi.spyOn(api, "rpTurn").mockResolvedValue(
      { job_id: "j2", message: null } as any);
    render(<RoleplayView />);

    fireEvent.click(await screen.findByText("New scene"));
    fireEvent.change(await screen.findByPlaceholderText(/flat for dinner/),
                     { target: { value: "She has just got in." } });
    fireEvent.click(await screen.findByText("Start"));

    await waitFor(() => expect(create).toHaveBeenCalledWith("p1", {
      character_id: "alice", scene_context: "She has just got in.",
    }));
    // No player message: the cast open the scene.
    await waitFor(() => expect(turn).toHaveBeenCalledWith("p1", "rc2", ""));
  });

  it("remembers the open scene across a remount, since views unmount on tab switch", async () => {
    const view = render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    await screen.findByText("You're early.");
    view.unmount();

    render(<RoleplayView />);
    // Straight back into the scene rather than the empty state.
    expect(await screen.findByText("You're early.")).toBeTruthy();
  });

  it("streams a reply in progress and parses it as it arrives", async () => {
    useStore.setState({
      jobs: [{ id: "j9", kind: "rp.reply", status: "running", payload: { conv_id: "rc1" },
               project_id: "p1", progress: 0.2, label: "", node_id: null, result: {},
               error: "", created_at: "", started_at: null, finished_at: null }] as any,
      streams: { j9: { text: 'Alice (neutral, cold): "Sure it was."', done: false } },
    } as any);
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    expect(await screen.findByText("Sure it was.")).toBeTruthy();
    // Composer locked while the cast are mid-reply, with a way to stop them.
    expect(await screen.findByLabelText("Stop")).toBeTruthy();
  });

  it("places a row menu against the viewport, not inside the scrolling log", async () => {
    // `.rp-log` scrolls, so it clips its descendants: the old absolutely
    // positioned popover on the last message was cut off at the log's bottom
    // edge and read as sitting behind the composer. The menu is now placed by
    // PopMenu from the trigger's own rect, so an inline top/left is the proof
    // that JS positioned it rather than CSS anchoring it to a clipped parent.
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    await screen.findByText("You're early.");
    fireEvent.click(screen.getAllByLabelText("More")[0]);

    const menu = document.querySelector(".rp-pop-menu") as HTMLElement | null;
    expect(menu).toBeTruthy();
    expect(menu!.style.top).not.toBe("");
    expect(menu!.style.left).not.toBe("");
    expect(await screen.findByText("Rewind to here")).toBeTruthy();
  });

  it("folds the scene context away once the scene has started", async () => {
    // Pinned open it sat above every reply for the rest of the conversation.
    vi.spyOn(api, "rpThread").mockResolvedValue({
      ...conv({ title: "Session one", untitled: false }),
      messages: [message({ id: "m1", role: "cast", text: "She waits." })],
    } as any);
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));

    const details = (await screen.findByText("Scene context"))
      .closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
  });

  it("keeps the context open while the scene is still empty", async () => {
    // With nothing said yet it is the only thing there is to read.
    vi.spyOn(api, "rpThread").mockResolvedValue({
      ...conv({ title: "Session one", untitled: false }), messages: [],
    } as any);
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));

    const details = (await screen.findByText("Scene context"))
      .closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(true);
  });

  it("does not give the player's message the bare .player class", async () => {
    // It used to, and `.player` is the Play view's stage wrapper:
    // `background: #06070a; flex-direction: column`. So every player message
    // wore a full-width black band and lost its right alignment, because a
    // column direction turns `justify-content: flex-end` from "right" into
    // "bottom". Nothing failed and no test noticed; it just looked broken.
    // Generic class names in a shared stylesheet is exactly the hazard the
    // `rp-` prefix exists for, and this one slipped through un-prefixed.
    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    await screen.findByText("You're early.");
    const mine = document.querySelector(".rp-msg.me");
    expect(mine).toBeTruthy();
    expect(mine!.classList.contains("player")).toBe(false);
    expect(document.querySelector(".rp-msg.player")).toBe(null);
  });

  it("ports a conversation into the canvas and goes to the new scene", async () => {
    const port = vi.spyOn(api, "rpPort").mockResolvedValue({ job_id: "jp" } as any);
    vi.spyOn(api, "getJob").mockResolvedValue({
      id: "jp", status: "done", result: { scene_id: "n_new" } } as any);
    const focusNode = vi.fn();
    useStore.setState({ focusNode, refreshGraph: vi.fn() } as any);

    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    fireEvent.click(await screen.findByText("Port to scene"));

    await waitFor(() => expect(port).toHaveBeenCalledWith("p1", "rc1"));
    // The port is worth nothing if it leaves you looking at the conversation.
    // 3s: the completion poll behind the port job ticks at 1.5s, like the one
    // behind End scene.
    await waitFor(() => expect(focusNode).toHaveBeenCalledWith("n_new", "canvas"),
                  { timeout: 3000 });
  });

  it("asks before porting a second time, because it builds a second scene", async () => {
    vi.spyOn(api, "rpThread").mockResolvedValue({
      ...conv({ scene_id: "n_old" }),
      messages: [message({ id: "m1", role: "cast", text: "She waits." })],
    } as any);
    const port = vi.spyOn(api, "rpPort").mockResolvedValue({ job_id: "jp" } as any);

    render(<RoleplayView />);
    fireEvent.click(await screen.findByText("First dinner"));
    fireEvent.click(await screen.findByText("Port again"));
    // Nothing written yet -- the copy is one way, so a second one is a second
    // scene and the author should know that before it happens.
    expect(port).not.toHaveBeenCalled();
    expect(await screen.findByText(/builds a second one beside it/)).toBeTruthy();
  });
});
