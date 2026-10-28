import { useState } from "react";
import { useStore } from "../lib/store";

/**
 * A branch point owns its own destinations — a Choice, Hub or Condition has no
 * spare "out" socket to send anywhere, and an End or Jump is already a
 * destination decision. Everything else can simply be pointed at a scene.
 */
export function canLinkOn(kind?: string): boolean {
  return !["choice", "hub", "condition", "jump", "end", "loop"].includes(kind ?? "");
}

/**
 * "Go to scene …" — the missing way to end a scene and start the next one.
 *
 * A scene transition is just an edge. Every Scene container compiles to a
 * label, so wiring the last line of a path into another scene's node emits
 * `jump sc_…` and the story carries on there; no Jump node in between, which
 * is how a hand-built graph reads best and what the existing projects already
 * do. A Jump node is worth it only when you want the hop to be a visible step
 * of its own, and the inspector gives that one the same dropdown.
 *
 * Not to be confused with an End node, which finishes the playthrough.
 */
export default function NextSceneLink({ fromId, fromKind, label, hint }: {
  fromId: string | null;
  fromKind?: string;
  /** Renders the picker as its own inspector section under this heading. */
  label?: string;
  hint?: string;
}) {
  const store = useStore();
  const [busy, setBusy] = useState(false);

  const scenes = store.nodes.filter((n) => n.type === "scene");
  if (!fromId || !canLinkOn(fromKind) || scenes.length === 0) return null;

  const already = store.edges.some(
    (e) => e.source === fromId && e.source_handle === "out");

  const picker = (
    <select
      style={{ width: 190 }}
      value=""
      disabled={busy}
      title={already
        ? "Send this path into a different scene"
        : "Send this path on into another scene when it ends"}
      onChange={async (e) => {
        const sceneId = e.target.value;
        if (!sceneId) return;
        setBusy(true);
        try {
          await store.connect(fromId, sceneId, "out");
          const scene = store.nodes.find((n) => n.id === sceneId);
          store.notify(`continues into “${scene?.data?.title || "the next scene"}”`);
        } catch (err) {
          store.fail(String(err));
        } finally {
          setBusy(false);
        }
      }}
    >
      <option value="">{already ? "Go to another scene…" : "Go to scene…"}</option>
      {scenes.map((scene) => (
        <option key={scene.id} value={scene.id}>
          → {scene.data?.title || "Untitled scene"}
        </option>
      ))}
    </select>
  );

  if (!label) return picker;
  return (
    <>
      <div className="section-title">{label}</div>
      {hint && <div className="hint" style={{ marginBottom: 6 }}>{hint}</div>}
      {picker}
    </>
  );
}
