import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background, BackgroundVariant, Controls, MiniMap, ReactFlowProvider,
  applyNodeChanges, type Connection, type Edge, type Node, type NodeChange,
  type NodeMouseHandler, type OnConnect, type ReactFlowState, useReactFlow,
  useStore as useFlowStore,
} from "reactflow";
import "reactflow/dist/style.css";
import { cgContext } from "../lib/cg";
import { layoutGraph, heightFor, widthFor } from "../lib/layout";
import { turnRanks, type Box } from "../lib/route";
import { useStore } from "../lib/store";
import FlowEdge, { ObstacleContext } from "./FlowEdge";
import NodeCard, { type CardData, portsFor } from "./NodeCard";

const nodeTypes = { vn: NodeCard };
const edgeTypes = { flow: FlowEdge };

/** Every card's rendered size as one string: it changes when a card is
 *  measured or resized, not when one is dragged or the view pans. */
const sizesKey = (s: ReactFlowState) => {
  let key = "";
  s.nodeInternals.forEach((n) => { key += `${n.id}:${n.width}x${n.height};`; });
  return key;
};

/** Sentinel values for the scene picker. */
const NEW_SCENE = "__new_scene__";
const NO_SCENE = "__no_scene__";

function CanvasInner() {
  const store = useStore();
  const { fitView, setCenter, getViewport, screenToFlowPosition, getNodes } = useReactFlow();
  const [flowNodes, setFlowNodes] = useState<Node<CardData>[]>([]);
  const [activeScene, setActiveScene] = useState<string | null>(null);
  const dragging = useRef<Record<string, { x: number; y: number }>>({});
  const wrapperRef = useRef<HTMLDivElement>(null);

  const characters = useMemo(
    () => Object.fromEntries(store.characters.map((c) => [c.id, c])),
    [store.characters],
  );
  const problemIds = useMemo(
    () => new Set(store.problems.filter((p) => p.severity === "error").map((p) => p.node_id)),
    [store.problems],
  );
  const runningJobs = useMemo(() => {
    const map: Record<string, number> = {};
    for (const job of store.jobs) {
      if (job.node_id && (job.status === "running" || job.status === "queued")) {
        map[job.node_id] = job.progress;
      }
    }
    return map;
  }, [store.jobs]);

  // Scenes are picked one at a time -- rendering every scene on one graph stops
  // being manageable well before the node count that a single VN reaches.
  const scenes = useMemo(
    () => store.nodes
      .filter((n) => n.type === "scene")
      .sort((a, b) => a.y - b.y || a.x - b.x || a.id.localeCompare(b.id)),
    [store.nodes],
  );

  // Nodes the backend could not attribute to any Scene (a leading node before
  // the first scene, a dangling import). They still need somewhere to live.
  const hasLooseNodes = useMemo(
    () => store.nodes.some((n) => n.type !== "scene" && !store.sceneOf[n.id]),
    [store.nodes, store.sceneOf],
  );

  const sceneIsValid = useCallback((id: string | null) => {
    if (!id) return false;
    if (id === NO_SCENE) return hasLooseNodes;
    return scenes.some((s) => s.id === id);
  }, [scenes, hasLooseNodes]);

  // Settle on a scene: keep the current one if it still exists, else the last
  // one this project was on, else the first scene.
  useEffect(() => {
    if (!store.project) return;
    const key = `vn:activeScene:${store.project.id}`;
    setActiveScene((current) => {
      if (sceneIsValid(current)) return current;
      let saved: string | null = null;
      try { saved = localStorage.getItem(key); } catch { /* private mode */ }
      if (sceneIsValid(saved)) return saved;
      return scenes[0]?.id ?? (hasLooseNodes ? NO_SCENE : null);
    });
  }, [store.project?.id, scenes, hasLooseNodes, sceneIsValid]);

  useEffect(() => {
    if (!store.project || !activeScene) return;
    try {
      localStorage.setItem(`vn:activeScene:${store.project.id}`, activeScene);
    } catch { /* private mode / quota */ }
  }, [store.project?.id, activeScene]);

  // Selecting a node from the sidebar, search or the gallery jumps to the
  // scene it lives in. `focusRequest` is in the deps so a jump back to the
  // node that is already selected still moves the canvas.
  useEffect(() => {
    if (!store.selected) return;
    const owner = store.sceneOf[store.selected]
      ?? (scenes.some((s) => s.id === store.selected) ? store.selected : null);
    if (owner) {
      if (owner !== activeScene) setActiveScene(owner);
    } else if (hasLooseNodes && activeScene !== NO_SCENE) {
      setActiveScene(NO_SCENE);
    }
  }, [store.selected, store.focusRequest]);

  const visibleNodes = useMemo(() => {
    if (activeScene === NO_SCENE) {
      return store.nodes.filter((n) => n.type !== "scene" && !store.sceneOf[n.id]);
    }
    if (!activeScene) return [];
    return store.nodes.filter(
      (n) => n.id === activeScene || store.sceneOf[n.id] === activeScene,
    );
  }, [store.nodes, store.sceneOf, activeScene]);

  const visibleIds = useMemo(
    () => new Set(visibleNodes.map((n) => n.id)),
    [visibleNodes],
  );

  const visibleEdges = useMemo(
    () => store.edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target)),
    [store.edges, visibleIds],
  );

  // Edges that leave this scene (a choice branch or a hop into another scene's
  // start). We can't draw them here, so name where they go in a hint.
  const exits = useMemo(
    () => store.edges.filter((e) => visibleIds.has(e.source) && !visibleIds.has(e.target)),
    [store.edges, visibleIds],
  );
  const exitCount = exits.length;
  const exitTargets = useMemo(() => {
    const names = exits.map((e) => {
      const target = store.nodes.find((n) => n.id === e.target);
      const owner = target && (store.sceneOf[target.id] ?? target.id);
      const scene = store.nodes.find((n) => n.id === owner);
      return scene?.data?.title || target?.slug || "elsewhere";
    });
    return [...new Set(names)].join(", ");
  }, [exits, store.nodes, store.sceneOf]);

  // Which lines play over a CG held from above. One walk up the path per
  // visible line, so it is scoped to the scene on screen.
  const underHeldCg = useMemo(() => {
    const at = cgContext(store.nodes, store.edges);
    return new Set(visibleNodes
      .filter((n) => (n.type === "say" || n.type === "narration") && at(n.id).held)
      .map((n) => n.id));
  }, [store.nodes, store.edges, visibleNodes]);

  // Rebuild the React Flow view whenever the IR or the active scene changes.
  useEffect(() => {
    // A port counts as wired even when its target sits in another scene and
    // isn't drawn here -- otherwise a cross-scene branch looks unconnected.
    const wiredByNode: Record<string, Record<string, boolean>> = {};
    for (const edge of store.edges) {
      if (visibleIds.has(edge.source)) {
        (wiredByNode[edge.source] ??= {})[edge.source_handle] = true;
      }
    }
    setFlowNodes(
      visibleNodes.map((node) => ({
        id: node.id,
        type: "vn",
        position: { x: node.x, y: node.y },
        selected: store.selected === node.id,
        data: {
          node,
          label: store.labels[node.id],
          speaker: node.type === "say"
            ? characters[node.data?.speaker]?.display_name ?? node.data?.speaker
            : undefined,
          speakerColor: characters[node.data?.speaker]?.color,
          problem: problemIds.has(node.id),
          wired: wiredByNode[node.id] ?? {},
          progress: runningJobs[node.id],
          underHeldCg: underHeldCg.has(node.id),
        },
      })),
    );
  }, [visibleNodes, visibleIds, store.edges, store.labels, store.selected,
      characters, problemIds, runningJobs, underHeldCg]);

  // What edges route around: each card at its saved position, at the size it
  // actually rendered where React Flow has measured it.
  const sizes = useFlowStore(sizesKey);
  const obstacles: Box[] = useMemo(() => {
    const rendered = new Map(getNodes().map((n) => [n.id, n]));
    return visibleNodes.map((node) => {
      const r = rendered.get(node.id);
      return {
        id: node.id, left: node.x, top: node.y,
        right: node.x + (r?.width ?? widthFor(node)),
        bottom: node.y + (r?.height ?? heightFor(node)),
      };
    });
  }, [visibleNodes, sizes, getNodes]);

  const flowEdges: Edge[] = useMemo(() => {
    const byId = new Map(visibleNodes.map((n) => [n.id, n]));

    // A branching node's edges leave from ports spread along its bottom edge.
    // Stagger where each one turns so neighbours heading the same way don't
    // draw their horizontals over each other.
    const fans = new Map<string, { id: string; from: number; to: number }[]>();
    for (const edge of visibleEdges) {
      const source = byId.get(edge.source);
      const target = byId.get(edge.target);
      if (!source || !target || !["choice", "hub", "condition"].includes(source.type)) continue;
      const ports = portsFor(source);
      const index = ports.findIndex((p) => p.id === edge.source_handle);
      if (index < 0) continue;
      const fan = fans.get(source.id) ?? [];
      fan.push({
        id: edge.id,
        from: source.x + ((index + 1) / (ports.length + 1)) * widthFor(source),
        to: target.x + widthFor(target) / 2,
      });
      fans.set(source.id, fan);
    }
    const turns = new Map([...fans.values()].flatMap((fan) => [...turnRanks(fan)]));

    return visibleEdges.map((edge) => {
      const source = byId.get(edge.source);
      const ports = source ? portsFor(source) : [];
      const port = ports.find((p) => p.id === edge.source_handle);
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.source_handle,
        type: "flow",
        data: { turn: turns.get(edge.id) ?? 0 },
        label: port && ports.length > 1 ? port.name.slice(0, 22) : undefined,
        animated: false,
        style: { strokeWidth: 1.6 },
      };
    });
  }, [visibleEdges, visibleNodes]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setFlowNodes((current) => applyNodeChanges(changes, current));
    for (const change of changes) {
      if (change.type === "position" && change.position) {
        dragging.current[change.id] = change.position;
      }
    }
    const finished = changes.some((c) => c.type === "position" && c.dragging === false);
    if (finished) {
      const positions = Object.entries(dragging.current).map(([id, p]) => ({ id, ...p }));
      dragging.current = {};
      store.persistPositions(positions);
    }
  }, [store]);

  const onConnect: OnConnect = useCallback((conn: Connection) => {
    if (!conn.source || !conn.target) return;
    store.connect(conn.source, conn.target, conn.sourceHandle ?? "out");
  }, [store]);

  const onNodeClick: NodeMouseHandler = useCallback((_, node) => {
    store.select(node.id);
  }, [store]);

  const autoLayout = useCallback(async () => {
    const nodes = visibleNodes;
    if (!nodes.length) return;
    const measured = Object.fromEntries(
      getNodes().map((n) => [n.id, { width: n.width ?? undefined, height: n.height ?? undefined }]),
    );
    const positions = layoutGraph(nodes, visibleEdges, measured);

    // Keep one node fixed under the viewport through the re-flow instead of
    // zooming out to the whole scene: the selection, else whatever is nearest
    // the centre of the screen right now.
    let anchorId =
      store.selected && nodes.some((n) => n.id === store.selected)
        ? store.selected
        : null;
    if (!anchorId && wrapperRef.current && nodes.length) {
      const r = wrapperRef.current.getBoundingClientRect();
      const c = screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
      let best = Infinity;
      for (const n of nodes) {
        const d = (n.x + 120 - c.x) ** 2 + (n.y + heightFor(n) / 2 - c.y) ** 2;
        if (d < best) { best = d; anchorId = n.id; }
      }
    }
    const from = anchorId ? nodes.find((n) => n.id === anchorId) : undefined;
    const to = anchorId ? positions.find((p) => p.id === anchorId) : undefined;

    await store.persistPositions(positions);

    if (from && to) {
      const { zoom } = getViewport();
      const cy = to.y + heightFor(from) / 2;
      setTimeout(() => setCenter(to.x + 120, cy, { zoom, duration: 400 }), 60);
    }
  }, [store, visibleNodes, visibleEdges, getViewport, getNodes, screenToFlowPosition, setCenter]);

  const createScene = useCallback(async () => {
    const maxY = store.nodes.reduce((m, n) => Math.max(m, n.y), 0);
    const node = await store.addNode("scene", { title: "New scene" },
      { x: 80, y: maxY + 240 });
    if (node) setActiveScene(node.id);
  }, [store]);

  // Fit the new scene into view when switching -- unless the switch was caused
  // by selecting a node, in which case the selection effect centres on it.
  useEffect(() => {
    if (!activeScene) return;
    if (store.selected && visibleNodes.some((n) => n.id === store.selected)) return;
    const timer = setTimeout(() => fitView({ duration: 300, padding: 0.18 }), 60);
    return () => clearTimeout(timer);
  }, [activeScene]);

  // Focus whatever the sidebar, search or gallery selects.
  useEffect(() => {
    if (!store.selected) return;
    const node = visibleNodes.find((n) => n.id === store.selected);
    if (node) {
      setCenter(node.x + 120, node.y + heightFor(node) / 2, { zoom: 1, duration: 350 });
    }
  }, [store.selected, store.focusRequest, activeScene]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if ((event.key === "Delete" || event.key === "Backspace") && store.selected) {
        event.preventDefault();
        store.removeNode(store.selected);
      }
      if (event.key === "l" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        autoLayout();
      }
      if (event.key.toLowerCase() === "z" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        if (event.shiftKey) store.redo(); else store.undo();
      }
      if (event.key.toLowerCase() === "y" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        store.redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [store.selected, autoLayout]);

  if (!store.nodes.length) {
    return (
      <div className="empty-state">
        <h2>Empty graph</h2>
        <p>
          Add a Scene node from the palette to start. A Scene container emits the
          label and resets the stage; everything else hangs off it.
        </p>
        <button className="primary" onClick={() => store.addNode("scene", { title: "Opening" })}>
          Add opening scene
        </button>
      </div>
    );
  }

  return (
    <ObstacleContext.Provider value={obstacles}>
      <ReactFlow
        ref={wrapperRef}
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onPaneClick={() => store.select(null)}
        onEdgesDelete={(edges) => edges.forEach((e) => store.disconnect(e.id))}
        deleteKeyCode={null}
        minZoom={0.08}
        maxZoom={2}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#232833" />
        <Controls showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          nodeColor={(n) => MINIMAP_COLORS[(n.data as CardData)?.node?.type ?? ""] ?? "#4a5468"}
          maskColor="rgba(15,17,22,0.72)"
        />
        <div className="canvas-toolbar">
          <select
            className="scene-picker"
            value={activeScene ?? ""}
            onChange={(e) => {
              if (e.target.value === NEW_SCENE) { createScene(); return; }
              store.select(null);
              setActiveScene(e.target.value);
            }}
          >
            {!activeScene && <option value="" disabled>Select a scene…</option>}
            {scenes.map((scene) => (
              <option key={scene.id} value={scene.id}>
                {scene.data?.title || "Untitled scene"}
              </option>
            ))}
            {hasLooseNodes && <option value={NO_SCENE}>(unassigned nodes)</option>}
            <option value={NEW_SCENE}>＋ New scene…</option>
          </select>
          <button onClick={autoLayout} title="Auto-layout (Ctrl+L)">Auto-layout</button>
          <button title="Zooms out until every node in this scene is on screen"
            onClick={() => fitView({ duration: 400, padding: 0.15 })}>Fit</button>
          <button disabled={!store.canUndo} onClick={() => store.undo()}
            title={store.undoLabel ? `Undo ${store.undoLabel} (Ctrl+Z)` : "Nothing to undo"}>↶</button>
          <button disabled={!store.canRedo} onClick={() => store.redo()}
            title={store.redoLabel ? `Redo ${store.redoLabel} (Ctrl+Shift+Z)` : "Nothing to redo"}>↷</button>
          {exitCount > 0 && (
            <span className="pill" title={`Leads into: ${exitTargets}`}>
              {exitCount} link{exitCount === 1 ? "" : "s"} out
            </span>
          )}
        </div>
      </ReactFlow>
    </ObstacleContext.Provider>
  );
}

const MINIMAP_COLORS: Record<string, string> = {
  scene: "#bb9af7", say: "#7aa2f7", narration: "#5c6577", choice: "#e8b84b",
  hub: "#d8a13f", condition: "#56cfc4", set: "#9d7bd8", stage: "#4d8ab5",
  end: "#f2707a",
};

export default function Canvas() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}
