import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "./App";

/**
 * The actual shape of the refresh-loop bug.
 *
 * AuthGate was correct in isolation, but App called `store.init()` from its own
 * mount effect — outside the gate. So on a PIN-protected server the workspace
 * fetched, got a 401, the API layer reloaded the page, and the cycle repeated:
 * a permanent "Connecting…" and no PIN form.
 *
 * This asserts the property that was violated: while locked, the app makes no
 * data requests and never reloads.
 */

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });

let reloadSpy: ReturnType<typeof vi.fn>;
let requested: string[];

beforeEach(() => {
  requested = [];
  reloadSpy = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, reload: reloadSpy },
  });

  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    requested.push(url);
    if (url.includes("/api/auth/status")) {
      return json({ required: true, authorised: false });
    }
    // Anything else while locked is the bug: reject it the way the server would.
    return json({ detail: "access token required" }, 401);
  }) as any;
});

afterEach(() => vi.restoreAllMocks());

it("makes no data requests and never reloads while the PIN gate is locked", async () => {
  render(<App />);

  expect(await screen.findByText("Enter PIN")).toBeTruthy();

  // Give any stray mount effects a chance to fire before asserting.
  await new Promise((resolve) => setTimeout(resolve, 60));

  const dataCalls = requested.filter((url) => !url.includes("/api/auth/"));
  expect(dataCalls).toEqual([]);
  expect(reloadSpy).not.toHaveBeenCalled();
  expect(screen.queryByText("Connecting…")).toBeNull();
});

it("loads the workspace once the gate reports an open server", async () => {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    requested.push(url);
    if (url.includes("/api/auth/status")) return json({ required: false, authorised: true });
    if (url.includes("/api/projects")) return json([]);
    if (url.includes("/api/vocab")) return json({ tags: {}, emotions: [], expressions: [] });
    if (url.includes("/api/settings")) return json({});
    return json({});
  }) as any;

  render(<App />);

  // The workspace mounts and only then starts fetching project data.
  await waitFor(() => expect(requested.some((u) => u.includes("/api/projects"))).toBe(true));
  expect(reloadSpy).not.toHaveBeenCalled();
});
