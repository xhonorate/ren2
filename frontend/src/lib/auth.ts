/**
 * Auth signalling between the API layer and the PIN gate.
 *
 * The API layer must never reload the page to deal with a 401: the workspace
 * fetches on mount, so a reload turns one rejected request into an endless
 * refresh loop and the PIN form never gets to render. Instead a 401 re-locks
 * the gate in place, and the request fails normally.
 */

type Listener = () => void;

const listeners = new Set<Listener>();

export function onUnauthorized(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function reportUnauthorized(): void {
  for (const listener of listeners) listener();
}

export class UnauthorizedError extends Error {
  constructor() {
    super("This editor is locked. Enter the PIN to continue.");
    this.name = "UnauthorizedError";
  }
}
