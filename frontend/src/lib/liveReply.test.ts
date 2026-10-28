import { describe, expect, it } from "vitest";
import { parseLiveReply } from "./liveReply";

describe("reading a reply as it is written", () => {
  it("splits spoken lines from narration and drops the directions", () => {
    const beats = parseLiveReply(
      'SFX: a kettle clicks off\n' +
      'Aiko (smirking, amused, 0.6): "You\'re up early."\n' +
      "She pours two cups without asking.\n" +
      "CG: leaning on the counter\n");
    expect(beats).toEqual([
      { kind: "say", name: "Aiko", tags: "smirking, amused, 0.6",
        text: "You're up early.", partial: false },
      { kind: "narration", text: "She pours two cups without asking.", partial: false },
    ]);
  });

  it("shows a spoken line while its words are still arriving", () => {
    const beats = parseLiveReply('Aiko (soft_smile, warm): "Drink it before');
    expect(beats).toEqual([
      { kind: "say", name: "Aiko", tags: "soft_smile, warm", text: "Drink it before",
        partial: true },
    ]);
  });

  it("holds back a line that could still become either kind", () => {
    // A name and half a bracket is going to be a bubble; showing it as
    // narration first makes the line jump.
    expect(parseLiveReply("Aiko (smi")).toEqual([]);
    expect(parseLiveReply("She pours")).toEqual([]);
    expect(parseLiveReply("MUS")).toEqual([]);
    // Long enough, or punctuated, and it can only be narration.
    expect(parseLiveReply("She pours two cups")[0]?.kind).toBe("narration");
  });

  it("stops at a choice or continuation block", () => {
    const beats = parseLiveReply('Aiko: "Well?"\nCONTINUE:\n- she leaves');
    expect(beats.map((b) => b.text)).toEqual(["Well?"]);
  });

  it("reads a finished one-line reply, which a partial last line would swallow", () => {
    // What a stored message usually is. Without `done` the only line counts as
    // still being written, and a reply that is one line of narration renders as
    // nothing at all.
    expect(parseLiveReply("She pours", true)).toEqual([
      { kind: "narration", text: "She pours", partial: false },
    ]);
    expect(parseLiveReply('Aiko (smirking, amused): "You came."', true)).toEqual([
      { kind: "say", name: "Aiko", tags: "smirking, amused", text: "You came.",
        partial: false },
    ]);
  });

  it("strips a closing quote even mid-stream, so it never doubles on screen", () => {
    // The views draw the quote marks themselves. A line that has closed its
    // quote has finished its sentence, whether or not the newline has arrived.
    expect(parseLiveReply('Aiko (neutral, cold): "Sure it was."')[0].text)
      .toBe("Sure it was.");
  });

  it("does not take a sentence with a colon in it for a speaker", () => {
    const beats = parseLiveReply("The note says one thing: come alone.\n");
    expect(beats).toEqual([
      { kind: "narration", text: "The note says one thing: come alone.", partial: false },
    ]);
  });
});
