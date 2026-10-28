/**
 * Undo/redo as a journal of inverse operations.
 *
 * Deliberately not snapshot-based: a snapshot of every node on every keystroke
 * is far too heavy, and restoring one would rewrite nodes that never changed.
 * Each entry carries its own `undo` and `redo`, so an edit costs one closure.
 *
 * The rule that constrains all of this: undoing a delete must restore the
 * *same* node id. A new id is a new Ren'Py label, which breaks player saves —
 * so deletes capture the full node and its edges, and restore them verbatim.
 */

export interface Command {
  label: string;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
}

const LIMIT = 100;

export class History {
  private past: Command[] = [];
  private future: Command[] = [];
  private suspended = false;
  private onChange?: () => void;

  constructor(onChange?: () => void) {
    this.onChange = onChange;
  }

  /** Record a command that has already been performed. */
  push(command: Command): void {
    if (this.suspended) return;
    this.past.push(command);
    if (this.past.length > LIMIT) this.past.shift();
    this.future = [];
    this.onChange?.();
  }

  /**
   * Merge rapid edits to the same target into one entry, so typing a sentence
   * is a single undo rather than forty.
   */
  pushCoalescing(key: string, command: Command, windowMs = 900): void {
    if (this.suspended) return;
    const last = this.past[this.past.length - 1];
    const now = Date.now();
    if (last && (last as any).__key === key && now - (last as any).__at < windowMs) {
      // Keep the oldest undo (back to before the burst), take the newest redo.
      (last as any).redo = command.redo;
      (last as any).__at = now;
      this.future = [];
      this.onChange?.();
      return;
    }
    (command as any).__key = key;
    (command as any).__at = now;
    this.push(command);
  }

  async undo(): Promise<string | null> {
    const command = this.past.pop();
    if (!command) return null;
    this.suspended = true;
    try {
      await command.undo();
      this.future.push(command);
      return command.label;
    } finally {
      this.suspended = false;
      this.onChange?.();
    }
  }

  async redo(): Promise<string | null> {
    const command = this.future.pop();
    if (!command) return null;
    this.suspended = true;
    try {
      await command.redo();
      this.past.push(command);
      return command.label;
    } finally {
      this.suspended = false;
      this.onChange?.();
    }
  }

  /** Run work without recording it (used while applying an undo). */
  async silently<T>(fn: () => Promise<T>): Promise<T> {
    this.suspended = true;
    try {
      return await fn();
    } finally {
      this.suspended = false;
    }
  }

  clear(): void {
    this.past = [];
    this.future = [];
    this.onChange?.();
  }

  get canUndo(): boolean {
    return this.past.length > 0;
  }

  get canRedo(): boolean {
    return this.future.length > 0;
  }

  get nextUndoLabel(): string | null {
    return this.past[this.past.length - 1]?.label ?? null;
  }

  get nextRedoLabel(): string | null {
    return this.future[this.future.length - 1]?.label ?? null;
  }
}
