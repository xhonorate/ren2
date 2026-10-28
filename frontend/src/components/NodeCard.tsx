import { Handle, Position, type NodeProps } from "reactflow";
import { api } from "../lib/api";
import { widthFor } from "../lib/layout";
import { statusIcon, useStore } from "../lib/store";
import type { IRNode } from "../lib/types";

export interface CardData {
  node: IRNode;
  label?: string;
  speaker?: string;
  speakerColor?: string;
  problem?: boolean;
  wired: Record<string, boolean>;
  progress?: number;
  /** A CG held from an earlier line is still on screen during this one. */
  underHeldCg?: boolean;
}

function Badge({ kind, status }: { kind: string; status: string }) {
  if (status === "none") return null;
  return (
    <span className={`badge ${status}`} title={`${kind}: ${status}`}>
      {kind[0].toUpperCase()}
      {statusIcon[status] ?? ""}
    </span>
  );
}

export default function NodeCard({ data, selected }: NodeProps<CardData>) {
  const { node, label, speaker, speakerColor, problem, wired, progress, underHeldCg } = data;
  const d = node.data ?? {};
  // In the head row rather than under the thumbnail, so a marker never changes
  // a card's height and the auto-layout's reserved space stays right.
  const cgMark = d.cg && d.cg_hold
    ? { text: "CG ⇣", title: "This CG stays up for the lines after it" }
    : underHeldCg && !d.cg
      ? (d.cg_clear
        ? { text: "CG ✕", title: "Takes the held CG down before this line" }
        : { text: "⇣ CG", title: "Plays over a CG held from an earlier line" })
      : null;

  const ports = portsFor(node);
  // Choice / Hub / Condition branch out along the bottom edge, not the side
  // (item 11).
  const bottomPorts = ["choice", "hub", "condition"].includes(node.type);
  const classes = [
    "vn-node",
    `node-${node.type}`,
    bottomPorts ? "branch-node" : "",
    selected ? "selected" : "",
    problem ? "problem" : "",
    node.orphaned ? "orphaned" : "",
  ].filter(Boolean).join(" ");

  return (
    <div className={classes} style={bottomPorts ? { width: widthFor(node) } : undefined}>
      <Handle type="target" position={Position.Top} />

      <div className="head">
        <span className="kind">{node.type}</span>
        {speaker && <span className="speaker" style={{ color: speakerColor }}>{speaker}</span>}
        {node.pinned_label && <span title="pinned as a jump target">📌</span>}
        {cgMark && <span className="cg-mark" title={cgMark.title}>{cgMark.text}</span>}
        <span className="badges">
          <Badge kind="text" status={node.text_status} />
          <Badge kind="image" status={node.image_status} />
          <Badge kind="audio" status={node.audio_status} />
        </span>
      </div>

      <div className="text">{summarize(node)}</div>
      {d.image_hash && (
        // The CG itself, not just a "CG" badge -- which shot a beat got is the
        // one thing a badge cannot tell you, and opening the inspector on every
        // node in turn to find out is how you lose an afternoon. `loading=lazy`
        // keeps a scene full of CGs off the wire until it is scrolled to.
        <div className="node-thumb">
          <img src={api.assetUrl(d.image_hash)} loading="lazy" decoding="async"
            alt="" draggable={false} />
        </div>
      )}
      {metaLine(node) && <div className="meta">{metaLine(node)}</div>}
      {label && <div className="label-tag" title={label}>{label}</div>}

      {ports.length > 0 && bottomPorts && (
        <div className="ports-bottom">
          {ports.map((port, index) => (
            <div key={port.id} className={`bport ${wired[port.id] ? "wired" : ""}`}>
              <span className="name" title={port.name}>{port.name}</span>
              <Handle
                type="source"
                position={Position.Bottom}
                id={port.id}
                style={{ left: `${((index + 1) / (ports.length + 1)) * 100}%` }}
              />
            </div>
          ))}
        </div>
      )}

      {ports.length > 0 && !bottomPorts && (
        <div style={{ marginTop: 4 }}>
          {ports.map((port, index) => (
            <div key={port.id} className={`port ${wired[port.id] ? "wired" : ""}`}>
              <span className="dot" />
              <span className="name" title={port.name}>{port.name}</span>
              <Handle
                type="source"
                position={Position.Right}
                id={port.id}
                style={{ top: "auto", bottom: (ports.length - index - 1) * 22 + 11, right: -5 }}
              />
            </div>
          ))}
        </div>
      )}

      {ports.length === 0 && node.type !== "end" && (
        <Handle type="source" position={Position.Bottom} id="out" />
      )}

      {progress !== undefined && (
        <div className="progress"><div style={{ width: `${Math.round(progress * 100)}%` }} /></div>
      )}
    </div>
  );
}

export function portsFor(node: IRNode): { id: string; name: string }[] {
  const d = node.data ?? {};
  if (node.type === "choice") {
    return (d.options ?? []).map((o: any) => ({ id: o.id, name: o.label || "(unnamed)" }));
  }
  if (node.type === "hub") {
    // A route-targeted topic resolves at play time, so it has no port to wire.
    return (d.options ?? [])
      .filter((o: any) => !o.target_route)
      .map((o: any) => ({ id: o.id, name: o.label || "(unnamed)" }));
  }
  if (node.type === "condition") {
    const branches = (d.branches ?? []).map((b: any) => {
      const test = branchTest(b);
      return { id: b.id, name: test ? `if ${test}` : "else" };
    });
    // Only a branch with neither picked gates nor an expression is the else.
    if (d.has_else !== false && !(d.branches ?? []).some((b: any) => !branchTest(b))) {
      branches.push({ id: "else", name: "else" });
    }
    return branches;
  }
  if (node.type === "call") {
    return [{ id: "out", name: "call →" }, { id: "after", name: "then →" }];
  }
  return [];
}

/** What one condition branch tests, as the canvas shows it: picked gates and
 *  the typed expression, ANDed the way the compiler ANDs them. Empty means the
 *  branch is the `else`. */
function branchTest(branch: any): string {
  const parts = (branch.requires ?? [])
    .filter((g: any) => (g.stat ?? "").trim())
    .map((g: any) => {
      const v = g.character ? `${g.stat}_${g.character}` : g.stat;
      return `${v} ${g.op || ">="} ${g.value ?? ""}`.trim();
    });
  const expr = (branch.expr ?? "").trim();
  if (expr) parts.push(parts.length ? `(${expr})` : expr);
  return parts.join(" and ");
}

function summarize(node: IRNode): string {
  const d = node.data ?? {};
  switch (node.type) {
    case "scene":
      return d.title || "Untitled scene";
    case "say":
    case "narration":
      return d.text || "(empty)";
    case "choice":
    case "hub":
      return d.prompt || "(no prompt)";
    case "condition":
      return (d.branches ?? []).map((b: any) => branchTest(b) || "else")
        .join("  ·  ") || "(no branches)";
    case "set":
      return (d.assignments ?? [])
        .map((a: any) => `${a.var} ${a.op} ${a.value}`).join("\n") || "(no assignments)";
    case "stage": {
      const bits: string[] = [];
      if (d.location) bits.push(`→ ${d.location}`);
      else if (d.time_of_day) bits.push(d.time_of_day);
      if (d.weather) bits.push(d.weather);
      if (d.show?.length) bits.push(`show ${d.show.join(", ")}`);
      if (d.hide?.length) bits.push(`hide ${d.hide.join(", ")}`);
      const outfits = Object.entries(d.outfits ?? {});
      if (outfits.length) bits.push(outfits.map(([k, v]) => `${k}:${v}`).join(", "));
      // Music rides on the stage node and is often the only thing on it —
      // omitted here, a working node renders as "(no changes)" and reads as a
      // bug in the graph.
      if (d.stop_music) bits.push("stop music");
      else if (d.music_file || d.music_cue) bits.push(`♪ ${d.music_file || d.music_cue}`);
      return bits.join(" · ") || "(no changes)";
    }
    case "audio":
      return `${d.action ?? "play"} ${d.channel ?? "music"} ${d.file ?? ""}`.trim();
    case "end":
      return d.mode === "return" ? "return" : `→ ending_${d.ending_id || "?"}`;
    case "jump":
    case "call":
      // A route destination has no edge to read off the graph, so the card
      // has to say it -- otherwise the node looks unwired.
      if (String(d.target_route ?? "").trim()) {
        return `→ next in “${String(d.target_route).trim()}”`;
      }
      return d.target_label || "→ connected target";
    case "raw":
      return d.code || "(empty)";
    case "transition":
      return `with ${d.transition ?? "dissolve"}`;
    default:
      return "";
  }
}

function metaLine(node: IRNode): string {
  const d = node.data ?? {};
  if (node.type === "say") {
    const bits = [d.expression, d.emotion].filter(Boolean);
    if (d.intensity !== undefined) bits.push(`i${Number(d.intensity).toFixed(1)}`);
    if (d.cg) bits.push("CG");
    return bits.join(" · ");
  }
  if (node.type === "scene") {
    return [d.background, d.time_of_day, d.weather].filter(Boolean).join(" · ");
  }
  if (node.type === "choice") {
    const options = d.options ?? [];
    const bits = [`${options.length} options`, d.mode];
    const gated = options.filter(
      (o: any) => (o.requires ?? []).length || (o.condition ?? "").trim()).length;
    if (gated) bits.push(`${gated} gated`);
    if (d.exhaust) bits.push("menuset");
    if (d.loop_back) bits.push("loops");
    return bits.filter(Boolean).join(" · ");
  }
  if (node.type === "hub") {
    const options = d.options ?? [];
    const gated = options.filter((o: any) => (o.requires ?? []).length).length;
    const routed = options.filter((o: any) => o.target_route).length;
    const bits = [`${options.length} topics`];
    if (gated) bits.push(`${gated} gated`);
    if (routed) bits.push(`${routed} by route`);
    if (d.exhaust !== false) bits.push("menuset");
    return bits.join(" · ");
  }
  return "";
}
