import { describe, it, expect } from "vitest";
import { tokenAt } from "./TagInput";

/** What `accept()` does with the span, without mounting the component: replace
 *  [start, end) with the tag, and add a trailing ", " only when nothing
 *  follows. Kept in step with TagInput.accept. */
function insert(value: string, caret: number, tag: string): string {
  const [start, end] = tokenAt(value, caret);
  const rest = value.slice(end);
  const tail = rest.trim().length ? "" : ", ";
  return value.slice(0, start) + tag + tail + rest;
}

describe("the token under the caret", () => {
  it("starts at the tag, not at the separator's space", () => {
    // The bug: start landed on the space after the comma, so accepting a
    // suggestion overwrote it and produced "school uniform,brown loafers".
    const value = "school uniform, brow";
    const [start, , token] = tokenAt(value, value.length);
    expect(token).toBe("brow");
    expect(value[start]).toBe("b");
  });

  it("keeps the comma and space when a suggestion is accepted", () => {
    const value = "school uniform, brow";
    expect(insert(value, value.length, "brown loafers"))
      .toBe("school uniform, brown loafers, ");
  });

  it("completes a tag in the middle without touching its neighbours", () => {
    const value = "school uniform, brow, white socks";
    // Caret at the end of "brow".
    expect(insert(value, 20, "brown loafers"))
      .toBe("school uniform, brown loafers, white socks");
  });

  it("treats a newline as a separator, since the CG fields are textareas", () => {
    const value = "close-up,\nfrom bel";
    const [, , token] = tokenAt(value, value.length);
    expect(token).toBe("from bel");
    expect(insert(value, value.length, "from below"))
      .toBe("close-up,\nfrom below, ");
  });

  it("handles the first tag in an empty field", () => {
    expect(insert("clos", 4, "close-up")).toBe("close-up, ");
  });
});
