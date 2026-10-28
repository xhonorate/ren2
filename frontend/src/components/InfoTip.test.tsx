import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InfoTip, LabelText } from "./InfoTip";
import { Check, Section } from "./Inspector";

/**
 * The tooltip that replaced the inspector's paragraphs.
 *
 * Two things are worth holding onto here and neither is visible from the
 * markup. The popover has to leave the inspector's DOM subtree, because that
 * panel scrolls and would clip it at the panel edge exactly when it has
 * something long to say. And it has to flip above the icon near the bottom of
 * the window, or the explanation opens off-screen for every field at the end
 * of a long node.
 */

/** Put the icon at a chosen height, since jsdom gives every element a zero rect. */
function atViewportY(top: number) {
  Element.prototype.getBoundingClientRect = function () {
    return { top, bottom: top + 13, left: 40, right: 53, width: 13, height: 13,
             x: 40, y: top, toJSON: () => ({}) } as DOMRect;
  };
}

describe("InfoTip", () => {
  it("says nothing until it is asked", () => {
    render(<InfoTip>Feeds the label name.</InfoTip>);
    expect(screen.queryByRole("tooltip")).toBeNull();

    fireEvent.mouseEnter(screen.getByRole("button", { name: "What is this?" }));
    expect(screen.getByRole("tooltip").textContent).toBe("Feeds the label name.");
  });

  it("opens on keyboard focus too, and Escape closes it", () => {
    render(<InfoTip>Persistent flags survive save deletion.</InfoTip>);
    const icon = screen.getByRole("button", { name: "What is this?" });

    fireEvent.focus(icon);
    expect(screen.getByRole("tooltip")).toBeTruthy();
    fireEvent.keyDown(icon, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("renders outside the scrolling panel that holds the icon", () => {
    // The whole reason it is portaled: `.inspector` is a 372px column with
    // overflow-y, so it clips on both axes. A popover left inside it is cut
    // off precisely when it is long.
    const { container } = render(
      <div className="inspector"><InfoTip>Long explanation.</InfoTip></div>);

    fireEvent.mouseEnter(screen.getByRole("button", { name: "What is this?" }));
    const pop = screen.getByRole("tooltip");
    expect(container.querySelector(".inspector")!.contains(pop)).toBe(false);
    expect(document.body.contains(pop)).toBe(true);
  });

  it("hangs below the icon, and flips above it near the foot of the window", () => {
    const real = Element.prototype.getBoundingClientRect;
    try {
      window.innerHeight = 800;

      atViewportY(100);
      const { unmount } = render(<InfoTip>Room below.</InfoTip>);
      fireEvent.mouseEnter(screen.getByRole("button", { name: "What is this?" }));
      let style = screen.getByRole("tooltip").style;
      expect(style.top).toBe("119px");        // icon bottom + 6
      expect(style.bottom).toBe("");
      unmount();

      // Near the bottom there is no room, so it is anchored by its own bottom
      // edge instead -- which is what lets it clear the icon without anyone
      // having to measure how tall it turned out.
      atViewportY(760);
      render(<InfoTip>No room below.</InfoTip>);
      fireEvent.mouseEnter(screen.getByRole("button", { name: "What is this?" }));
      style = screen.getByRole("tooltip").style;
      expect(style.top).toBe("");
      expect(style.bottom).toBe("46px");      // innerHeight - icon top + 6
    } finally {
      Element.prototype.getBoundingClientRect = real;
    }
  });
});

describe("LabelText", () => {
  it("shows the field name with no icon when there is nothing to explain", () => {
    render(<LabelText label="Slug" />);
    expect(screen.getByText("Slug")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "What is this?" })).toBeNull();
  });

  it("keeps the icon beside the name rather than after the control", () => {
    const { container } = render(<LabelText label="Route" info="Optional." />);
    const group = container.querySelector(".lbl")!;
    expect(group.textContent).toBe("Route?");
    expect(group.querySelector(".info-tip")).toBeTruthy();
  });
});

describe("where the icon can and cannot sit", () => {
  it("keeps a section's tip out of its header button", () => {
    // A section header is a button and the tip is a button: nesting them is
    // invalid HTML, and the inner one stops receiving its own clicks.
    const { container } = render(
      <Section title="Music" info="Music is state, not an event.">
        <div>body</div>
      </Section>);

    const tip = container.querySelector(".info-tip")!;
    expect(tip).toBeTruthy();
    expect(tip.closest("button")).toBe(tip);
    expect(container.querySelector(".sec-head")!.contains(tip)).toBe(false);
  });

  it("keeps a checkbox's tip out of its label", () => {
    // Clicking a label toggles its control, so a tip inside one cannot be
    // hovered without ticking the box.
    const { container } = render(
      <Check label="NSFW" checked={false} onChange={() => {}}
        info="Explicitly, in the same voice as everything else." />);

    const tip = container.querySelector(".info-tip")!;
    expect(container.querySelector("label")!.contains(tip)).toBe(false);
  });
});
