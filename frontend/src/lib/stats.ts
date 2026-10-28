import type { StatBand, StatDef } from "./types";

/**
 * Reading a character stat's current band on the client.
 *
 * The rules here mirror `StatDef.band_for` and `graph.stat_var` on the backend
 * and have to keep mirroring them: the writer model is told one band in its
 * card and the author is shown another in the preview, and a disagreement
 * between those two is invisible until a scene comes back in the wrong
 * register.
 */

/** The band a value falls in -- the last one whose `min` it clears. */
export function bandFor(stat: StatDef, value: unknown): StatBand | null {
  if (!stat.bands?.length) return null;
  // Guards that make this agree with Python's `float(value)`, which is what
  // the backend converts with. `Number()` alone does not: it reads null,
  // undefined and "" as 0, so a flag with no value would report the bottom
  // band -- "distant" for a relationship the story has said nothing about.
  // A bool does convert, there and here, because `float(False)` is 0.0.
  if (value === null || value === undefined) return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  let match: StatBand | null = null;
  for (const band of [...stat.bands].sort((a, b) => a.min - b.min)) {
    if (n >= band.min) match = band;
  }
  return match;
}

/**
 * The stat a `{stat}_{character}` flag belongs to, or null for a plain flag.
 *
 * Longest id wins. Character ids contain underscores (`ms_victoria`), so the
 * separator cannot be used to split the name -- and with stats `affection` and
 * `affection_peak` both declared, the short one would otherwise claim
 * `affection_peak_alice`.
 */
export function statForFlag(stats: StatDef[], name: string): StatDef | null {
  let best: StatDef | null = null;
  for (const stat of stats) {
    if (!stat.id || !name.startsWith(`${stat.id}_`)) continue;
    if (!best || stat.id.length > best.id.length) best = stat;
  }
  return best;
}

/** The band label for a flag, or "" when it is not a banded stat. */
export function bandLabelFor(
  stats: StatDef[], name: string, value: unknown,
): string {
  const stat = statForFlag(stats, name);
  return (stat ? bandFor(stat, value)?.label : "") || "";
}

/** `34 (warming)`, or just the value for anything that is not a banded stat. */
export function describeFlag(stats: StatDef[], name: string, value: unknown): string {
  const label = bandLabelFor(stats, name, value);
  return label ? `${value} (${label})` : String(value);
}

/** Every banded stat flag's current band label, for spotting a crossing. */
export function bandLabels(
  stats: StatDef[], flags: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, value] of Object.entries(flags)) {
    const stat = statForFlag(stats, name);
    const band = stat ? bandFor(stat, value) : null;
    if (band?.label) out[name] = band.label;
  }
  return out;
}
