import type { PreviewOp, PreviewProgram } from "./types";

/** The block a program compiled with `from_node` starts in. */
export const FROM_NODE = "__from_node__";

export interface Frame {
  ops: PreviewOp[];
  index: number;
  label: string;
}

export interface Sprite {
  character: string;
  name: string;
  outfit: string;
  pose: string;
  expression: string;
  position: string;
  sprite: string | null;
  /** The node that put the character in this state -- what clicking the
   *  sprite opens. Null when a restored stage could not name one. */
  node: string | null;
}

export interface Backdrop {
  id: string | null;
  time: string | null;
  node: string | null;
}

/** An event CG over the stage. `node` owns the picture -- for a held CG that
 *  is the line it started on, not the line on screen. */
export interface StageCg {
  node: string | null;
  image: string;
}

export interface Stage {
  background: Backdrop;
  sprites: Sprite[];
  cg: StageCg | null;
}

export const EMPTY_STAGE: Stage = {
  background: { id: null, time: null, node: null },
  sprites: [],
  cg: null,
};

export type Stop =
  | { kind: "say"; op: PreviewOp }
  | { kind: "menu"; op: PreviewOp }
  /** An End node: the stage it reached stays up. */
  | { kind: "end"; message: string }
  /** Ran out of script, or could not go on. */
  | { kind: "halt"; message: string };

export interface RunResult {
  stop: Stop;
  frames: Frame[];
  flags: Record<string, any>;
  stage: Stage;
  /** The stage just before the stopping node's own ops ran. Re-running that
   *  node after an edit starts from here, so the edit shows against the stage
   *  this playthrough actually built rather than the canonical path's. */
  before: Stage;
  seen: Set<string>;
  /** Lines for the history log that are not dialogue. */
  notes: string[];
}

/**
 * Run the program from `frames` until something needs the player: a line, a
 * menu, or the end. Pure -- the caller commits the result -- so the same run
 * serves advancing and re-running the line on screen after an edit.
 */
export function run(program: PreviewProgram, input: {
  frames: Frame[];
  flags: Record<string, any>;
  stage: Stage;
  visited: Set<string>;
}): RunResult {
  // Frames are copied field by field, not just the array: `frame.index` is
  // advanced below, and the Frame objects are the ones held in React state.
  // Mutating them makes a run non-idempotent, so StrictMode's second
  // invocation resumes past the ops the first one already ran.
  const frames = input.frames.map((frame) => ({ ...frame }));
  const state = { ...input.flags };
  let bg = input.stage.background;
  let sprs = [...input.stage.sprites];
  let cg: StageCg | null = input.stage.cg ?? null;
  // A route dispatched mid-run has to see the labels this same run entered.
  const seen = new Set(input.visited);
  const notes: string[] = [];
  let markNode: string | null = null;
  let before: Stage = { background: bg, sprites: [...sprs], cg };

  const finish = (stop: Stop): RunResult => ({
    stop, frames, flags: state, stage: { background: bg, sprites: sprs, cg }, before, seen, notes,
  });
  /** Where a jump/call op actually goes: a route resolves to its next
   *  unplayed scene, falling back to the wired target once it runs out. */
  const destination = (op: PreviewOp): string | null =>
    (op.route ? nextInRoute(program, op.route, seen) ?? op.target ?? null : op.target ?? null);

  for (let guard = 0; guard < 4000; guard += 1) {
    if (!frames.length) return finish({ kind: "halt", message: "end of script" });
    const frame = frames[frames.length - 1];
    if (frame.index >= frame.ops.length) {
      frames.pop();
      continue;
    }
    const op = frame.ops[frame.index];
    frame.index += 1;

    // Restored setup is not the node's own work, so it never moves the mark.
    if (op.node && !op.restored && op.node !== markNode) {
      markNode = op.node;
      before = { background: bg, sprites: [...sprs], cg };
    }
    const source: string | null = op.restored ? op.source ?? null : op.node ?? null;

    switch (op.op) {
      case "scene":
        bg = { id: op.background ?? null, time: op.time_of_day ?? null, node: source };
        if (!op.keep_sprites) sprs = [];
        // `scene` wipes the layer a held CG is on; a Stage node that stays
        // put compiles to no `scene` and leaves it.
        if (!op.keep_cg) cg = null;
        break;
      case "cg":
        cg = op.image ? { node: source, image: op.image } : null;
        break;
      case "show": {
        // The player is never drawn as a sprite; ignore any stray show op.
        if (program.characters[op.character]?.is_player) break;
        const entry: Sprite = {
          character: op.character, name: op.name, outfit: op.outfit,
          pose: op.pose || "front", expression: op.expression, position: op.position,
          sprite: op.sprite ?? null, node: source,
        };
        const at = sprs.findIndex((s) => s.character === op.character);
        if (at >= 0) sprs[at] = entry; else sprs.push(entry);
        break;
      }
      case "hide":
        sprs = sprs.filter((s) => s.character !== op.character);
        break;
      case "set":
        applyEffect(state, op.var, op.operator, op.value);
        break;
      case "say":
        return finish({ kind: "say", op });
      case "menu":
        return finish({ kind: "menu", op });
      case "if": {
        // A branch is taken when its picked gates and its expression both
        // hold -- the same AND the Ren'Py compiler emits. Neither one means
        // the else.
        const branch = (op.branches ?? []).find(
          (b: any) => gatesMet(b.requires, state) && evaluate(b.expr, state));
        if (branch?.ops?.length) {
          frames.push({ ops: branch.ops, index: 0, label: frame.label });
        }
        break;
      }
      case "jump": {
        const target = destination(op);
        const block = target ? program.blocks[target] : null;
        if (!target || !block) {
          return finish({
            kind: "halt",
            message: op.route
              ? `“${op.route}” has no scene left to play, and nothing follows it`
              : `jump to a missing label (${op.target ?? "none"})`,
          });
        }
        frames.pop();
        frames.push({ ops: block, index: 0, label: target });
        seen.add(target);
        break;
      }
      case "call": {
        const target = destination(op);
        const block = target ? program.blocks[target] : null;
        if (target && block) frames.push({ ops: block, index: 0, label: target });
        if (target) seen.add(target);
        break;
      }
      case "end":
        return finish({
          kind: "end",
          message: op.mode === "return" ? "return" : `ending: ${op.ending_id}`,
        });
      case "unlock":
        state[`persistent.${op.name}`] = true;
        break;
      case "return":
        frames.pop();
        break;
      case "raw":
        notes.push("[raw Ren'Py — not run in preview]");
        break;
      default:
        break;
    }
  }
  return finish({
    kind: "halt",
    message: "stopped: the preview hit its step limit (a loop with no dialogue?)",
  });
}

/** The next scene in `route` this run has not played, or null once it is
 *  exhausted -- the browser twin of the generated `vn_next_in_route`. */
export function nextInRoute(program: PreviewProgram, route: string,
                            seen: Set<string>): string | null {
  return (program.routes?.[route] ?? []).find((label) => !seen.has(label)) ?? null;
}

/** One `{var, op, value}` effect, as a Set node or a menu option applies it. */
export function applyEffect(state: Record<string, any>, name: string, operator: string,
                            raw: any): void {
  const value = coerce(raw);
  const current = state[name] ?? (typeof value === "number" ? 0 : value);
  if (operator === "+=") state[name] = Number(current) + Number(value);
  else if (operator === "-=") state[name] = Number(current) - Number(value);
  else state[name] = value;
}

/** Expression evaluation for `if` and per-option conditions. */
export function evaluate(expr: string, scope: Record<string, any>): boolean {
  if (!(expr ?? "").trim()) return true;
  try {
    const keys = Object.keys(scope);
    // eslint-disable-next-line no-new-func
    const fn = new Function(...keys, `"use strict"; return (${expr});`);
    return Boolean(fn(...keys.map((k) => scope[k])));
  } catch {
    return false;
  }
}

/** Every picked gate on an option or branch. Deliberately not an eval: the
 *  values come from the same `{var, op, value}` triples the Ren'Py compiler
 *  builds its `if` clause from, so the two agree on what is locked. */
export function gatesMet(requires: any, scope: Record<string, any>): boolean {
  return (requires ?? []).every((req: any) =>
    compare(scope[req.var], req.op, coerce(req.value)));
}

function compare(left: any, op: string, right: any): boolean {
  switch (op) {
    case "==": return left === right;
    case "!=": return left !== right;
    case ">": return Number(left) > Number(right);
    case ">=": return Number(left) >= Number(right);
    case "<": return Number(left) < Number(right);
    case "<=": return Number(left) <= Number(right);
    default: return false;
  }
}

export function requirementText(option: any): string {
  const parts = (option.requires ?? []).map(
    (req: any) => `${req.var} ${req.op} ${req.value}`);
  const expr = (option.condition ?? "").trim();
  if (expr) parts.push(expr);
  if (option.route) parts.push(`next unplayed scene in “${option.route}”`);
  return parts.join(" and ");
}

export function coerce(raw: any): any {
  const value = String(raw ?? "").trim();
  if (value.toLowerCase() === "true") return true;
  if (value.toLowerCase() === "false") return false;
  const num = Number(value);
  return Number.isNaN(num) || value === "" ? value.replace(/^["']|["']$/g, "") : num;
}

// -- assets on stage ---------------------------------------------------------

/** The sprite_sheet key -- `images.sprite_slot` on the backend. */
export function spriteSlot(outfit: string, pose: string, expression: string): string {
  return `${outfit}/${pose || "front"}/${expression || "neutral"}`;
}

/**
 * The closest generated sprite to what the stage asks for, read from the live
 * sheet so a regenerated sprite shows without recompiling. Mirrors
 * `_nearest_sprite` in backend/app/compiler/preview.py -- outfit held hardest,
 * pose given up first -- and has to stay in step with it.
 */
export function nearestSprite(sheet: Record<string, string> | undefined, outfit: string,
                              pose: string, expression: string):
    { slot: string; hash: string } | null {
  const slots = sheet ?? {};
  for (const slot of [
    spriteSlot(outfit, pose, expression),
    spriteSlot(outfit, "front", expression),
    spriteSlot(outfit, pose, "neutral"),
    spriteSlot(outfit, "front", "neutral"),
    // Pre-pose keys, so a character not regenerated since poses landed
    // still shows something.
    `${outfit}/${expression}`,
    `${outfit}/neutral`,
  ]) {
    if (slots[slot]) return { slot, hash: slots[slot] };
  }
  for (const slot of Object.keys(slots).sort()) {
    if (slots[slot] && slot.split("/")[0] === outfit) return { slot, hash: slots[slot] };
  }
  return null;
}

/** The background variant shown for a time of day: that variant, else the
 *  location's first. */
export function backgroundFor(variants: Record<string, string> | undefined,
                              time: string | null): { variant: string; hash: string } | null {
  const all = variants ?? {};
  if (time && all[time]) return { variant: time, hash: all[time] };
  const first = Object.entries(all)[0];
  return first ? { variant: first[0], hash: first[1] } : null;
}

/** Keys for a render queued from the player, so the stage and the edit panel
 *  agree on what is in progress. */
export const spriteRenderKey = (sprite: Pick<Sprite, "character" | "outfit" | "pose" | "expression">) =>
  `sprite:${sprite.character}:${spriteSlot(sprite.outfit, sprite.pose, sprite.expression)}`;

export const backgroundRenderKey = (locationId: string, variant: string) =>
  `bg:${locationId}:${variant}`;
