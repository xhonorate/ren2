import {
  useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from "react";
import { api } from "../lib/api";
import { LabelText } from "./InfoTip";

/** Booru-tag autocomplete for any field whose text reaches ComfyUI verbatim.
 *
 *  The reason this exists: a mistyped tag does not fail, it conditions on
 *  nothing. `school unifrom` renders happily and silently without a uniform,
 *  and the only way to notice is to read the image carefully and wonder. So
 *  every field that gets pasted into a prompt -- a CG's camera and action, a
 *  character's marks and accessories, an outfit's tags, a location's tags,
 *  every negative -- suggests from the dictionary the checkpoint was actually
 *  trained on.
 *
 *  Completion is scoped to the comma-separated token under the caret, because
 *  these fields are all tag *lists*: replacing the whole field would throw
 *  away everything typed before the one being edited.
 */

export interface TagHit {
  name: string;
  count: number;
  category: string;
  curated?: boolean;
}

/** The token under the caret, as [start, end, text].
 *
 *  Splitting on commas only. Newlines count as separators too -- the CG
 *  fields are textareas and people press enter between tags.
 *
 *  `start` skips the whitespace after the separator, so the span is the tag
 *  itself and not the space in front of it. Without that, accepting a
 *  suggestion overwrote the separator's own space and landed the tag as
 *  `school uniform,brown loafers` -- a comma with nothing after it, in fields
 *  whose text reaches ComfyUI verbatim.
 */
export function tokenAt(value: string, caret: number): [number, number, string] {
  const before = value.slice(0, caret);
  let start = Math.max(before.lastIndexOf(","), before.lastIndexOf("\n")) + 1;
  while (start < caret && /\s/.test(value[start])) start++;
  let end = caret;
  while (end < value.length && value[end] !== "," && value[end] !== "\n") end++;
  return [start, end, value.slice(start, end).trim()];
}

/** Every tag currently in the field, for checking them against the corpus. */
function splitTags(value: string): string[] {
  return (value ?? "").split(/[,\n]/).map((t) => t.trim()).filter(Boolean);
}

/**
 * The tags in this field that do not exist.
 *
 * A misspelled tag does not fail, it conditions on nothing -- `school unifrom`
 * renders happily and silently without a uniform. Director output has been
 * screened for this since `clean_cg_tags`; the record fields that reach
 * ComfyUI verbatim never were, and one real project shipped `public hair` and
 * a location tagged `cou`.
 *
 * Weighted tags are stripped before checking: `(hug:1.25)` is a real tag with
 * emphasis on it, not an unknown one.
 */
function useUnknownTags(value: string) {
  const [unknown, setUnknown] = useState<{ tag: string; suggestion: string }[]>([]);
  const seq = useRef(0);
  const tags = splitTags(value)
    .map((t) => t.replace(/^\((.+):[\d.]+\)$/, "$1").trim())
    .filter(Boolean);
  const key = tags.join(",");

  useEffect(() => {
    if (!key) {
      setUnknown([]);
      return;
    }
    const mine = ++seq.current;
    const timer = setTimeout(async () => {
      try {
        const res = await api.tagCheck(key.split(","));
        if (seq.current === mine) setUnknown(res.unknown);
      } catch {
        if (seq.current === mine) setUnknown([]);
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [key]);

  return unknown;
}

function useTagSearch(query: string, prefer?: string[]) {
  const [hits, setHits] = useState<TagHit[]>([]);
  const seq = useRef(0);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setHits([]);
      return;
    }
    // Debounced, and last-write-wins: typing fast otherwise lets an earlier
    // request land after a later one and show suggestions for a prefix the
    // field no longer has.
    const mine = ++seq.current;
    const timer = setTimeout(async () => {
      try {
        const res = await api.tagSearch(q, 10, prefer);
        if (seq.current === mine) setHits(res.results);
      } catch {
        if (seq.current === mine) setHits([]);
      }
    }, 120);
    return () => clearTimeout(timer);
  }, [query, prefer?.join(",")]);

  return hits;
}

interface Props {
  label?: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  /** Render a textarea instead of a single-line input. */
  rows?: number;
  mono?: boolean;
  /** Rank these dictionary categories first. Deliberately a preference and
   *  not a filter: the classifier gets ~99% of the popular tags right and
   *  about half of the long tail, so filtering would hide real tags it
   *  guessed wrong about. */
  prefer?: string[];
  hint?: string;
}

export default function TagInput({
  label, value, onChange, placeholder, rows, mono, prefer, hint,
}: Props) {
  const ref = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const [caret, setCaret] = useState(0);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const [, , token] = useMemo(
    () => tokenAt(value ?? "", caret), [value, caret]);
  const hits = useTagSearch(open ? token : "", prefer);
  const unknown = useUnknownTags(value ?? "");

  /** Swap one tag for its canonical spelling, leaving the rest of the field. */
  const replaceTag = useCallback((from: string, to: string) => {
    const pattern = new RegExp(
      `(^|[,\\n]\\s*)${from.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=\\s*($|[,\\n]))`);
    onChange((value ?? "").replace(pattern, (_m, lead) => `${lead}${to}`));
  }, [value, onChange]);

  useEffect(() => { setActive(0); }, [token]);

  // Keep the caret where the typist left it.
  //
  // These fields are controlled, and several of them save through the server
  // and get the saved record back. When that echo lands a beat later, React
  // writes the string into the element again -- and assigning `value` on a
  // focused input parks the caret at the end, so a word typed into the middle
  // of a tag list threw the next keystroke onto the end of the field. It only
  // showed up sometimes because it needs the round trip to land mid-word.
  //
  // The guard is narrow on purpose: restore only when the DOM says "end of
  // field" while we know the caret was elsewhere. A caret the user actually
  // moved is never fought, because `caret` tracks those too (onSelect).
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || document.activeElement !== el) return;
    const end = (value ?? "").length;
    if (el.selectionStart === el.selectionEnd
        && el.selectionStart === end
        && caret < end) {
      el.setSelectionRange(caret, caret);
    }
  }, [value]);

  const accept = useCallback((tag: string) => {
    const el = ref.current;
    const current = value ?? "";
    const [start, end] = tokenAt(current, el ? el.selectionStart ?? caret : caret);
    // Land the tag with a trailing ", " so the next one can just be typed --
    // unless there is already text after it, where that would be a stray comma.
    const rest = current.slice(end);
    const tail = rest.trim().length ? "" : ", ";
    const next = current.slice(0, start) + tag + tail + rest;
    onChange(next);
    setOpen(false);
    // Put the caret after what we inserted, so typing continues in the right
    // place rather than jumping to the end of the field.
    const at = start + tag.length + tail.length;
    requestAnimationFrame(() => {
      if (!el) return;
      el.focus();
      el.setSelectionRange(at, at);
      setCaret(at);
    });
  }, [value, onChange, caret]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open || hits.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % hits.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + hits.length) % hits.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      // Enter must not submit or newline while a suggestion is highlighted.
      e.preventDefault();
      accept(hits[active].name);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  const shared = {
    ref: ref as any,
    value: value ?? "",
    placeholder,
    onKeyDown,
    onChange: (e: any) => {
      onChange(e.target.value);
      setCaret(e.target.selectionStart ?? 0);
      setOpen(true);
    },
    onClick: (e: any) => setCaret(e.target.selectionStart ?? 0),
    // Every other way a caret moves -- arrows, home/end, drag-select, undo.
    // Tracking them all is what lets the restore above tell "React clobbered
    // it" apart from "the user went to the end on purpose".
    onSelect: (e: any) => setCaret(e.target.selectionStart ?? 0),
    onFocus: () => setOpen(true),
    // A click on a suggestion blurs the field first, so the list has to
    // outlive the blur long enough for the click to land.
    onBlur: () => setTimeout(() => setOpen(false), 150),
  };

  return (
    <div className="field" style={{ position: "relative" }}>
      {label && <label><LabelText label={label} info={hint} /></label>}
      {rows
        ? <textarea {...shared} rows={rows}
            style={mono ? { fontFamily: "var(--mono)", fontSize: 12 } : undefined} />
        : <input {...shared} />}

      {open && hits.length > 0 && (
        <div className="tag-suggest">
          {hits.map((hit, i) => (
            <div key={hit.name}
              className={`tag-suggest-row${i === active ? " active" : ""}`}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => { e.preventDefault(); accept(hit.name); }}>
              <span className="tag-suggest-name">{hit.name}</span>
              <span className="pill">{hit.category}</span>
              <span className="tag-suggest-count">
                {hit.curated ? "vocab" : formatCount(hit.count)}
              </span>
            </div>
          ))}
        </div>
      )}
      {unknown.length > 0 && (
        <div className="tag-unknown">
          <span title={
            "No Danbooru post carries this tag, so the renderer has nothing to "
            + "condition on — it does not fail, it just quietly does nothing."}>
            not a real tag:
          </span>
          {unknown.map((u) => (
            u.suggestion ? (
              <button key={u.tag} type="button" className="link-ish"
                title={`Replace with ${u.suggestion}`}
                onClick={() => replaceTag(u.tag, u.suggestion)}>
                {u.tag} → {u.suggestion}
              </button>
            ) : (
              <span key={u.tag} className="mono">{u.tag}</span>
            )
          ))}
        </div>
      )}
    </div>
  );
}

/** Post counts are only ever read as an order of magnitude -- whether the
 *  checkpoint has seen this tag a lot or barely at all. */
function formatCount(n: number): string {
  if (!n) return "";
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}k`;
  return String(n);
}
