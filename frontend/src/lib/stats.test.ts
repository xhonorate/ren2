import { describe, expect, it } from "vitest";
import { bandFor, bandLabelFor, bandLabels, describeFlag, statForFlag } from "./stats";
import type { StatDef } from "./types";

/**
 * These rules are a second implementation of `StatDef.band_for` and
 * `graph.stat_var`, which live in Python. They have to agree: the writer model
 * is told one band in its character card and the author is shown another in
 * the preview, and a disagreement is invisible until a scene comes back in the
 * wrong register.
 */

const stat = (over: Partial<StatDef> = {}): StatDef => ({
  id: "affection", name: "Affection", type: "int", default: "0",
  min: 0, max: 100, note: "", in_card: true,
  bands: [
    { min: 0, label: "distant", note: "" },
    { min: 20, label: "warming", note: "" },
    { min: 40, label: "close", note: "" },
  ],
  ...over,
});

describe("bandFor", () => {
  it("takes the last band the value clears", () => {
    const s = stat();
    expect(bandFor(s, 0)?.label).toBe("distant");
    expect(bandFor(s, 19)?.label).toBe("distant");
    expect(bandFor(s, 20)?.label).toBe("warming");   // inclusive lower edge
    expect(bandFor(s, 39)?.label).toBe("warming");
    expect(bandFor(s, 400)?.label).toBe("close");    // nothing clamps
  });

  it("does not care what order the bands were declared in", () => {
    const jumbled = stat({ bands: [
      { min: 40, label: "close", note: "" },
      { min: 0, label: "distant", note: "" },
      { min: 20, label: "warming", note: "" },
    ] });
    expect(bandFor(jumbled, 25)?.label).toBe("warming");
  });

  it("has no answer for a value that is not a number, or a stat with no bands", () => {
    expect(bandFor(stat(), "yes")).toBeNull();
    expect(bandFor(stat({ bands: [] }), 30)).toBeNull();
  });

  it("reports no band for an absent value rather than the bottom one", () => {
    // `Number()` reads all three as 0, so without guards a flag the story has
    // said nothing about would come back "distant" -- a claim about the
    // relationship, from missing data. Python's `float()` raises on each.
    expect(bandFor(stat(), null)).toBeNull();
    expect(bandFor(stat(), undefined)).toBeNull();
    expect(bandFor(stat(), "")).toBeNull();
    expect(bandFor(stat(), "   ")).toBeNull();
  });

  it("converts a numeric string and a bool, exactly as the backend does", () => {
    expect(bandFor(stat(), "34")?.label).toBe("warming");
    expect(bandFor(stat(), false)?.label).toBe("distant");   // float(False) == 0.0
    expect(bandFor(stat(), true)?.label).toBe("distant");    // 1, still under 20
  });
});

describe("statForFlag", () => {
  const stats = [stat(), stat({ id: "trust", name: "Trust" })];

  it("matches a stat to its per-character variable", () => {
    expect(statForFlag(stats, "affection_aiko")?.id).toBe("affection");
    expect(statForFlag(stats, "trust_aiko")?.id).toBe("trust");
  });

  it("handles a character id that contains an underscore", () => {
    // The separator cannot be used to split the name: `ms_victoria` is one id.
    expect(statForFlag(stats, "affection_ms_victoria")?.id).toBe("affection");
  });

  it("gives the longest matching stat the variable", () => {
    // With both declared, the short one would otherwise claim the long one's
    // variables and report the wrong ladder.
    const overlapping = [stat(), stat({ id: "affection_peak" })];
    expect(statForFlag(overlapping, "affection_peak_alice")?.id).toBe("affection_peak");
    expect(statForFlag(overlapping, "affection_alice")?.id).toBe("affection");
  });

  it("leaves an ordinary flag alone", () => {
    expect(statForFlag(stats, "knows_letter")).toBeNull();
    expect(statForFlag(stats, "affection")).toBeNull();   // no character
  });
});

describe("what the preview shows", () => {
  const stats = [stat()];

  it("labels a stat and leaves a plain flag as it is", () => {
    expect(describeFlag(stats, "affection_aiko", 34)).toBe("34 (warming)");
    expect(describeFlag(stats, "knows_letter", true)).toBe("true");
  });

  it("reports every banded stat's label, for spotting a crossing", () => {
    expect(bandLabels(stats, {
      affection_aiko: 41, affection_claire: 5, knows_letter: true,
    })).toEqual({ affection_aiko: "close", affection_claire: "distant" });
  });

  it("says nothing rather than something wrong for an unbanded value", () => {
    expect(bandLabelFor(stats, "knows_letter", true)).toBe("");
    expect(bandLabelFor([], "affection_aiko", 34)).toBe("");
  });
});
