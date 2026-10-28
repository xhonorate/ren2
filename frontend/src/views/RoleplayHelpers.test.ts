/**
 * The pure helpers behind the Roleplay chat: reading a performance bracket,
 * which take is showing, and splitting a typed message into the three things it
 * can carry.
 *
 * Tested without rendering, which is why they are exported from the view.
 */
import { describe, expect, it } from "vitest";
import {
  emotionOf, intensityOf, multiSpeaker, splitPlayerParts, takeAxis,
} from "./RoleplayView";
import { parseLiveReply } from "../lib/liveReply";

const message = (over: Partial<any> = {}) => ({
  id: "m1", conv_id: "c1", seq: 0, role: "cast" as const, text: "",
  takes: [], active_take: null, voice: [], created_at: null, ...over,
});

describe("emotionOf", () => {
  it("takes the feeling, which is the second slot", () => {
    expect(emotionOf("smirking, amused, 0.6")).toBe("amused");
    expect(emotionOf("looking_away, embarrassed")).toBe("embarrassed");
  });

  it("falls back to the only tag when a line carries one", () => {
    // Several words are in both vocabularies, so a single tag means the voice
    // matches the face.
    expect(emotionOf("crying")).toBe("crying");
  });

  it("is empty rather than wrong when there is no bracket", () => {
    expect(emotionOf(undefined)).toBe("");
    expect(emotionOf("")).toBe("");
  });
});

describe("intensityOf", () => {
  it("finds the number wherever in the bracket it sits", () => {
    expect(intensityOf("smirking, amused, 0.6")).toBe(0.6);
    expect(intensityOf("neutral, cold, 0.2, too quick")).toBe(0.2);
  });

  it("ignores anything outside 0 to 1, so a stray number is not an intensity", () => {
    expect(intensityOf("neutral, angry, 7")).toBe(null);
  });

  it("is null when the writer left it out", () => {
    expect(intensityOf("smiling, happy")).toBe(null);
  });
});

describe("takeAxis", () => {
  it("reports the showing take, 1-based", () => {
    expect(takeAxis(message({
      takes: [{ id: "k1" }, { id: "k2" }, { id: "k3" }], active_take: "k2",
    }))).toEqual({ index: 2, total: 3 });
  });

  it("points at the last take when nothing is marked active", () => {
    // Which is what a freshly written reply looks like before a swipe.
    expect(takeAxis(message({ takes: [{ id: "k1" }, { id: "k2" }] })))
      .toEqual({ index: 2, total: 2 });
  });

  it("survives a message with no takes at all", () => {
    expect(takeAxis(message())).toEqual({ index: 0, total: 0 });
  });
});

describe("splitPlayerParts", () => {
  it("separates speech, action and steer", () => {
    expect(splitPlayerParts("Fine. *drops into the chair* [she should push]")).toEqual([
      { kind: "speech", text: "Fine." },
      { kind: "action", text: "drops into the chair" },
      { kind: "steer", text: "she should push" },
    ]);
  });

  it("handles an action in the middle of a line", () => {
    expect(splitPlayerParts("I know. *looks away* Really.")).toEqual([
      { kind: "speech", text: "I know." },
      { kind: "action", text: "looks away" },
      { kind: "speech", text: "Really." },
    ]);
  });

  it("returns plain speech unchanged", () => {
    expect(splitPlayerParts("Just this.")).toEqual([
      { kind: "speech", text: "Just this." },
    ]);
  });

  it("drops nothing and invents nothing on an empty message", () => {
    expect(splitPlayerParts("")).toEqual([]);
    expect(splitPlayerParts("   ")).toEqual([]);
  });
});

describe("multiSpeaker", () => {
  it("is true only when two different people talk in one reply", () => {
    const two = parseLiveReply(
      'Kade (frowning, irritated): "You told her."\n'
      + 'Mira (looking_away, embarrassed, 0.6): "I told her something."');
    expect(multiSpeaker(two)).toBe(true);
  });

  it("is false for one person, however many lines they get", () => {
    const one = parseLiveReply(
      'Mira (thinking, anxious): "Can I ask you something stupid."\n'
      + 'Mira (eyes_closed, anxious, 0.6): "No. Forget it."');
    expect(multiSpeaker(one)).toBe(false);
  });

  it("is false for narration alone -- there is nobody to name", () => {
    expect(multiSpeaker(parseLiveReply("She puts the kettle on."))).toBe(false);
  });
});
