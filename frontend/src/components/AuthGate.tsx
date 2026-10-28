import { useEffect, useState } from "react";
import { onUnauthorized } from "../lib/auth";

type Phase = "checking" | "locked" | "open" | "offline";

/**
 * Shown only when the server was started with a PIN (`serve.py --pin`).
 *
 * Children are not rendered until the gate is open, because the workspace
 * fetches as soon as it mounts. The PIN is exchanged for a session cookie, so
 * images, audio and the job event stream authenticate too — none of them can
 * send a header.
 */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [phase, setPhase] = useState<Phase>("checking");
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const check = async () => {
    try {
      const res = await fetch("/api/auth/status", { credentials: "same-origin" });
      if (!res.ok) throw new Error(`status ${res.status}`);
      const status = await res.json();
      setPhase(!status.required || status.authorised ? "open" : "locked");
    } catch {
      setPhase("offline");
    }
  };

  useEffect(() => { check(); }, []);

  // A 401 from anywhere in the app re-locks the gate in place. This is why the
  // API layer must not reload the page: the workspace would fetch, 401, reload,
  // and never get far enough to show this form.
  useEffect(() => onUnauthorized(() => {
    setPhase((current) => (current === "open" ? "locked" : current));
  }), []);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: pin }),
      });
      if (res.ok) {
        setPin("");
        setPhase("open");
      } else {
        setError("That PIN did not match.");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  if (phase === "checking") {
    return <div className="empty-state"><p className="hint">Connecting…</p></div>;
  }

  if (phase === "offline") {
    return (
      <div className="empty-state">
        <h2>Cannot reach the server</h2>
        <p className="hint" style={{ maxWidth: "40ch" }}>
          Nothing answered at <code>/api</code>. Check that the backend is
          running, and that this device is on the same network as it.
        </p>
        <button className="primary" onClick={() => { setPhase("checking"); check(); }}>
          Try again
        </button>
      </div>
    );
  }

  if (phase === "locked") {
    return (
      <div className="empty-state">
        <h2>Enter PIN</h2>
        <p className="hint" style={{ maxWidth: "34ch" }}>
          This editor is being served over the network and is protected by a
          shared code.
        </p>
        <form onSubmit={submit} className="pin-form">
          <input
            autoFocus
            type="password"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={pin}
            placeholder="PIN"
            onChange={(e) => setPin(e.target.value)}
          />
          <button className="primary" type="submit" disabled={busy || !pin}>
            {busy ? "…" : "Unlock"}
          </button>
        </form>
        {error && <div className="hint bad">{error}</div>}
      </div>
    );
  }

  return <>{children}</>;
}
