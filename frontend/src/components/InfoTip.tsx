import { useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * What a field means, behind an icon next to its name.
 *
 * These were paragraphs sitting open under their fields. Each one is read once
 * -- the first time you meet the field -- and then re-read forever after as
 * the thing between you and the next control, in a panel 372px wide where a
 * node's settings already do not fit on a screen.
 *
 * Portaled to the body rather than positioned in flow, because `.inspector`
 * scrolls and therefore clips on both axes: a popover left inside it gets cut
 * off at the panel edge exactly when it has something long to say. Fixed
 * coordinates are measured off the icon at open time, and flip above it when
 * the field sits near the bottom of the window.
 *
 * Its own module because both the inspector's fields and `TagInput` need it,
 * and importing it from `Inspector` (which imports `TagInput`) would be a
 * cycle.
 */
export function InfoTip({ children }: { children: ReactNode }) {
  const [at, setAt] = useState<
    null | { left: number; top?: number; bottom?: number; maxHeight: number }>(null);

  const open = (e: { currentTarget: HTMLElement }) => {
    const r = e.currentTarget.getBoundingClientRect();
    const below = window.innerHeight - r.bottom - 12;
    const flip = below < 150;
    setAt({
      left: Math.max(8, Math.min(r.left - 4, window.innerWidth - 276)),
      top: flip ? undefined : r.bottom + 6,
      // Anchored by its bottom edge when flipped, so the popover clears the
      // icon without anyone having to measure its height.
      bottom: flip ? window.innerHeight - r.top + 6 : undefined,
      maxHeight: Math.max(120, flip ? r.top - 18 : below),
    });
  };
  const close = () => setAt(null);

  return (
    <>
      <button type="button" className="info-tip" aria-label="What is this?"
        onMouseEnter={open} onFocus={open} onMouseLeave={close} onBlur={close}
        onKeyDown={(e) => { if (e.key === "Escape") close(); }}
        onClick={(e) => e.preventDefault()}>?</button>
      {at && createPortal(
        <span className="info-pop" role="tooltip" style={at}>{children}</span>,
        document.body)}
    </>
  );
}

/** A field label with its explanation tucked in beside it. */
export function LabelText({ label, info }: { label: ReactNode; info?: ReactNode }) {
  return <span className="lbl">{label}{info && <InfoTip>{info}</InfoTip>}</span>;
}
