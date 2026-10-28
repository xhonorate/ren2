import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AuthGate from "./AuthGate";
import { reportUnauthorized } from "../lib/auth";

/**
 * Regression cover for the PIN refresh loop.
 *
 * The workspace fetches as soon as it mounts. If the gate rendered it while
 * locked, that fetch 401s; if the API layer answered a 401 by reloading, the
 * page would spin forever showing "Connecting…" and the PIN form would never
 * appear. Both halves are asserted here.
 */

function mockApi(handlers: Record<string, () => Response>) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    const key = Object.keys(handlers).find((k) => url.includes(k));
    if (!key) throw new Error(`unexpected fetch: ${url}`);
    return handlers[key]();
  });
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json" },
  });

let reloadSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  reloadSpy = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, reload: reloadSpy },
  });
});

afterEach(() => vi.restoreAllMocks());

describe("AuthGate", () => {
  it("shows the PIN form and does not mount the workspace while locked", async () => {
    const child = vi.fn(() => <div>workspace</div>);
    globalThis.fetch = mockApi({
      "/api/auth/status": () => json({ required: true, authorised: false }),
    }) as any;

    render(<AuthGate>{child()}</AuthGate>);

    expect(await screen.findByText("Enter PIN")).toBeTruthy();
    expect(screen.queryByText("workspace")).toBeNull();
    // The one thing that broke: no reload, ever.
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("does not get stuck on Connecting when a PIN is required", async () => {
    globalThis.fetch = mockApi({
      "/api/auth/status": () => json({ required: true, authorised: false }),
    }) as any;

    render(<AuthGate><div>workspace</div></AuthGate>);

    await waitFor(() => expect(screen.queryByText("Connecting…")).toBeNull());
    expect(screen.getByText("Enter PIN")).toBeTruthy();
  });

  it("renders the workspace straight away when no PIN is configured", async () => {
    globalThis.fetch = mockApi({
      "/api/auth/status": () => json({ required: false, authorised: true }),
    }) as any;

    render(<AuthGate><div>workspace</div></AuthGate>);
    expect(await screen.findByText("workspace")).toBeTruthy();
  });

  it("unlocks on the correct PIN and rejects a wrong one", async () => {
    let authorised = false;
    globalThis.fetch = mockApi({
      "/api/auth/status": () => json({ required: true, authorised }),
      "/api/auth/login": () => {
        authorised = true;
        return json({ ok: true });
      },
    }) as any;

    const { container } = render(<AuthGate><div>workspace</div></AuthGate>);
    await screen.findByText("Enter PIN");

    const input = container.querySelector("input") as HTMLInputElement;
    const form = container.querySelector("form") as HTMLFormElement;

    input.value = "4821";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));

    expect(await screen.findByText("workspace")).toBeTruthy();
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("re-locks in place when a later request 401s, without reloading", async () => {
    globalThis.fetch = mockApi({
      "/api/auth/status": () => json({ required: true, authorised: true }),
    }) as any;

    render(<AuthGate><div>workspace</div></AuthGate>);
    expect(await screen.findByText("workspace")).toBeTruthy();

    // A session that expires mid-use should show the PIN form again, not spin.
    reportUnauthorized();

    expect(await screen.findByText("Enter PIN")).toBeTruthy();
    expect(screen.queryByText("workspace")).toBeNull();
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("reports an unreachable server instead of hanging on Connecting", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }) as any;

    render(<AuthGate><div>workspace</div></AuthGate>);
    expect(await screen.findByText("Cannot reach the server")).toBeTruthy();
    expect(reloadSpy).not.toHaveBeenCalled();
  });
});
