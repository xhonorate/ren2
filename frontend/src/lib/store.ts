import { create } from "zustand";
import { api } from "./api";
import { History } from "./history";
import type {
  Character, IREdge, IRNode, Job, Location, NodeType, Problem, Project,
  VariableDef, VariableInfo, Vocab,
} from "./types";

export type View =
  "canvas" | "script" | "roleplay" | "characters" | "setting" | "gallery"
  | "config" | "build" | "preview" | "routes";

export type CharacterTab =
  "general" | "appearance" | "outfits" | "personality" | "voice" | "sprite-lora" | "sprites" | "cg-lora";

interface PlayFrom {
  nodeId: string | null;
  flags: Record<string, any>;
}

interface State {
  projects: Project[];
  project: Project | null;
  nodes: IRNode[];
  edges: IREdge[];
  labels: Record<string, string>;
  sceneOf: Record<string, string>;
  characters: Character[];
  locations: Location[];
  jobs: Job[];
  problems: Problem[];
  vocab: Vocab | null;
  variables: VariableInfo[];
  settings: Record<string, any>;
  selected: string | null;
  view: View;
  // The Characters view's own selection, kept here (not local component
  // state) so it survives navigating away and back -- the view unmounts on
  // every tab switch, and local state would forget it every time.
  selectedCharacterId: string | null;
  characterTab: CharacterTab;
  // The Setting view's location selection, kept here for the same reason and
  // so the gallery can jump straight at a background's location.
  selectedLocationId: string | null;
  // Bumped every time something asks for the current selection to be *shown*,
  // not merely set. Selecting a node the canvas already had selected changes
  // no state, so a jump from the gallery back to a node you were last on
  // would otherwise centre nothing and open nothing -- which is exactly the
  // case that looked broken.
  focusRequest: number;
  loading: boolean;
  error: string | null;
  toast: string | null;
  orphanCount: number;
  showOrphans: boolean;
  playFrom: PlayFrom;
  steerNotes: Record<string, string>;
  canUndo: boolean;
  canRedo: boolean;
  undoLabel: string | null;
  redoLabel: string | null;
  /** Text a job has written so far, by job id -- a Roleplay reply as it is
   *  being written. Whole text per event, so a missed one costs nothing. */
  streams: Record<string, { text: string; done: boolean }>;

  init: () => Promise<void>;
  openProject: (id: string) => Promise<void>;
  createProject: (name: string) => Promise<void>;
  patchProject: (body: Partial<Project>) => Promise<void>;
  refreshGraph: () => Promise<void>;
  refreshCharacters: () => Promise<void>;
  refreshJobs: () => Promise<void>;
  refreshValidation: () => Promise<void>;
  refreshVariables: () => Promise<void>;
  saveVariables: (variables: VariableDef[]) => Promise<void>;
  setSetting: (key: string, value: any) => Promise<void>;
  ensureVariable: (name: string) => Promise<void>;
  addNode: (type: NodeType, data?: any, opts?: any) => Promise<IRNode | undefined>;
  insertAfter: (anchorId: string, handle: string, type: NodeType, data?: any)
    => Promise<IRNode | undefined>;
  patchNode: (id: string, body: any, coalesceKey?: string) => Promise<void>;
  removeNode: (id: string, heal?: boolean, cascade?: boolean) => Promise<void>;
  persistPositions: (positions: { id: string; x: number; y: number }[]) => Promise<void>;
  connect: (source: string, target: string, handle: string) => Promise<void>;
  disconnect: (edgeId: string) => Promise<void>;
  undo: () => Promise<void>;
  redo: () => Promise<void>;
  /** Put an edit made outside the store's own actions on the undo journal. */
  record: (label: string, undo: () => Promise<void>, redo: () => Promise<void>) => void;
  /** Merge one job's latest state in, for a view polling a job it waits on. */
  upsertJob: (job: Job) => void;
  select: (id: string | null) => void;
  setView: (view: View) => void;
  focusNode: (id: string, view?: View) => void;
  focusCharacter: (id: string, tab?: CharacterTab) => void;
  focusLocation: (id: string) => void;
  selectCharacter: (id: string | null) => void;
  selectLocation: (id: string | null) => void;
  setCharacterTab: (tab: CharacterTab) => void;
  setShowOrphans: (value: boolean) => void;
  playFromNode: (nodeId: string | null, flags?: Record<string, any>) => void;
  rememberSteerNote: (key: string, text: string) => void;
  notify: (message: string) => void;
  fail: (message: string) => void;
  subscribeEvents: (pid: string) => void;
}

let eventSource: EventSource | null = null;
let toastTimer: number | undefined;

function readSteerNotes(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem("vn:steerNotes") || "{}");
  } catch {
    return {};
  }
}

export const useStore = create<State>((set, get) => {
  const history = new History(() => set({
    canUndo: history.canUndo,
    canRedo: history.canRedo,
    undoLabel: history.nextUndoLabel,
    redoLabel: history.nextRedoLabel,
  }));

  /** Everything needed to put a node back exactly as it was. */
  const capture = (nodeId: string) => {
    const node = get().nodes.find((n) => n.id === nodeId);
    const edges = get().edges.filter((e) => e.source === nodeId || e.target === nodeId);
    return { node, edges };
  };

  const restore = async (snapshot: ReturnType<typeof capture>) => {
    if (!snapshot.node) return;
    await api.restoreNodes(get().project!.id, [snapshot.node], snapshot.edges);
    await get().refreshGraph();
  };

  /** Put a finished LLM generation on the undo journal so Ctrl+Z removes the
   *  block and Ctrl+Y re-applies it (item 10). */
  const recordGeneration = (kind: string, result: any) => {
    if (kind !== "llm.generate_scene" && kind !== "llm.regenerate_from_node") return;
    if (!result || !result.anchor_id || !(result.inserted?.length)) return;
    const project = get().project;
    if (!project) return;
    const desc = {
      gen_id: result.gen_id,
      anchor_id: result.anchor_id,
      handle: result.handle ?? "out",
      inserted: result.inserted ?? [],
      reattached_to: result.reattached_to ?? null,
      reattached_handles: result.reattached_handles ?? [],
      orphaned: result.orphaned ?? [],
      regen_head: result.regen_head ?? null,
    };
    history.push({
      label: kind === "llm.regenerate_from_node" ? "AI regenerate" : "AI continue",
      undo: async () => {
        await api.rollbackGeneration(project.id, desc);
        await get().refreshGraph();
      },
      redo: async () => {
        await api.reapplyGeneration(project.id, desc);
        await get().refreshGraph();
      },
    });
  };

  return {
    projects: [],
    project: null,
    nodes: [],
    edges: [],
    labels: {},
    sceneOf: {},
    characters: [],
    locations: [],
    jobs: [],
    problems: [],
    vocab: null,
    variables: [],
    settings: {},
    selected: null,
    view: "canvas",
    selectedCharacterId: null,
    characterTab: "general",
    selectedLocationId: null,
    focusRequest: 0,
    loading: false,
    error: null,
    toast: null,
    orphanCount: 0,
    showOrphans: false,
    playFrom: { nodeId: null, flags: {} },
    steerNotes: readSteerNotes(),
    canUndo: false,
    canRedo: false,
    undoLabel: null,
    redoLabel: null,
    streams: {},

    init: async () => {
      set({ loading: true });
      try {
        const [projects, vocab, settings] = await Promise.all([
          api.listProjects(), api.vocab(), api.getSettings(),
        ]);
        set({ projects, vocab, settings, loading: false });
        const last = localStorage.getItem("vn:lastProject");
        const target = projects.find((p) => p.id === last) ?? projects[0];
        if (target) await get().openProject(target.id);
      } catch (e) {
        set({ error: String(e), loading: false });
      }
    },

    openProject: async (id) => {
      set({ loading: true, selected: null, selectedCharacterId: null });
      history.clear();
      try {
        const project = await api.getProject(id);
        localStorage.setItem("vn:lastProject", id);
        set({ project });
        await Promise.all([
          get().refreshGraph(), get().refreshCharacters(), get().refreshJobs(),
          get().refreshVariables(),
        ]);
        get().subscribeEvents(id);
        await get().refreshValidation();
      } catch (e) {
        set({ error: String(e) });
      } finally {
        set({ loading: false });
      }
    },

    createProject: async (name) => {
      const project = await api.createProject(name);
      set({ projects: [project, ...get().projects] });
      await get().openProject(project.id);
    },

    patchProject: async (body) => {
      const current = get().project;
      if (!current) return;
      const project = await api.updateProject(current.id, body);
      set({
        project,
        projects: get().projects.map((p) => (p.id === project.id ? project : p)),
      });
    },

    refreshGraph: async () => {
      const project = get().project;
      if (!project) return;
      const graph = await api.getGraph(project.id, get().showOrphans);
      set({
        nodes: graph.nodes,
        edges: graph.edges,
        labels: graph.labels,
        sceneOf: graph.scene_of ?? {},
        orphanCount: graph.orphan_count,
      });
    },

    refreshCharacters: async () => {
      const project = get().project;
      if (!project) return;
      const [characters, locations] = await Promise.all([
        api.listCharacters(project.id), api.listLocations(project.id),
      ]);
      set({ characters, locations });
    },

    refreshJobs: async () => {
      const project = get().project;
      if (!project) return;
      set({ jobs: await api.listJobs(project.id) });
    },

    refreshValidation: async () => {
      const project = get().project;
      if (!project) return;
      try {
        set({ problems: (await api.validate(project.id)).problems });
      } catch {
        /* validation is advisory */
      }
    },

    refreshVariables: async () => {
      const project = get().project;
      if (!project) return;
      try {
        set({ variables: (await api.listVariables(project.id)).variables });
      } catch {
        /* the dropdown just falls back to free text */
      }
    },

    // One setting, without the Config page's save-everything round trip -- for
    // a control that lives next to the thing it affects (the CG batch picker)
    // and has to remember its value. PUT /settings merges per key, so this
    // never clobbers a setting somebody edited in the other tab.
    setSetting: async (key, value) => {
      set({ settings: { ...get().settings, [key]: value } });
      try {
        await api.putSettings({ [key]: value });
      } catch (e) {
        get().fail(String(e));
        set({ settings: await api.getSettings() });
      }
    },

    saveVariables: async (variables) => {
      const project = get().project;
      if (!project) return;
      await api.saveVariables(project.id, variables);
      await get().refreshVariables();
    },

    ensureVariable: async (name) => {
      const clean = name.trim();
      if (!clean) return;
      const existing = get().variables.find((v) => v.name === clean);
      if (existing && !existing.derived) return;   // already declared
      // A stat instance already has a definition and a starting value from the
      // schema. Declaring it as a story variable would *win* over the stat
      // (see graph.merged_flag_defaults) and quietly reset the character's
      // starting value -- so leave it alone.
      if (existing?.source === "stat") return;
      const declared = get().variables.filter((v) => !v.derived)
        .map(({ name, type, default: def, note }) => ({ name, type, default: def, note }));
      await get().saveVariables([
        ...declared,
        { name: clean, type: existing?.type ?? "bool", default: "", note: "" },
      ]);
    },

    addNode: async (type, data = {}, opts = {}) => {
      const project = get().project;
      if (!project) return undefined;
      try {
        const node = await api.createNode(project.id, { type, data, ...opts });
        await get().refreshGraph();
        set({ selected: node.id });
        get().refreshValidation();

        const snapshot = capture(node.id);
        history.push({
          label: `add ${type}`,
          undo: async () => {
            await api.deleteNode(project.id, node.id);
            await get().refreshGraph();
          },
          redo: async () => { await restore(snapshot); },
        });
        return node;
      } catch (e) {
        get().fail(String(e));
        return undefined;
      }
    },

    insertAfter: async (anchorId, handle, type, data = {}) => {
      const project = get().project;
      if (!project) return undefined;
      try {
        const out = await api.insertAfter(project.id, {
          anchor_id: anchorId, handle, type, data,
        });
        await get().refreshGraph();
        get().refreshValidation();

        const snapshot = capture(out.node.id);
        history.push({
          label: `insert ${type}`,
          undo: async () => {
            await api.deleteNode(project.id, out.node.id, true);
            await get().refreshGraph();
          },
          redo: async () => { await restore(snapshot); },
        });
        return out.node;
      } catch (e) {
        get().fail(String(e));
        return undefined;
      }
    },

    patchNode: async (id, body, coalesceKey) => {
      const project = get().project;
      if (!project) return;
      const before = get().nodes.find((n) => n.id === id);
      if (!before) return;

      // Optimistic: the editor should not wait on a round trip per keystroke.
      set({
        nodes: get().nodes.map((n) =>
          n.id === id ? { ...n, ...body, data: { ...n.data, ...(body.data ?? {}) } } : n),
      });

      try {
        const updated = await api.updateNode(project.id, id, body);
        set({ nodes: get().nodes.map((n) => (n.id === id ? updated : n)) });

        const inverse: any = {};
        if (body.data) {
          inverse.data = Object.fromEntries(
            Object.keys(body.data).map((k) => [k, (before.data ?? {})[k] ?? null]));
        }
        for (const key of Object.keys(body)) {
          if (key !== "data") inverse[key] = (before as any)[key];
        }
        const command = {
          label: "edit",
          undo: async () => {
            await api.updateNode(project.id, id, inverse);
            await get().refreshGraph();
          },
          redo: async () => {
            await api.updateNode(project.id, id, body);
            await get().refreshGraph();
          },
        };
        if (coalesceKey) history.pushCoalescing(coalesceKey, command);
        else history.push(command);
      } catch (e) {
        get().fail(String(e));
        await get().refreshGraph();
      }
    },

    removeNode: async (id, heal = false, cascade = false) => {
      const project = get().project;
      if (!project) return;
      // Capture every node the delete could touch so undo restores them all with
      // their original ids -- the labels must not move (item 12).
      const ids = cascade ? [id, ...descendantIds(get().nodes, get().edges, id)] : [id];
      const idSet = new Set(ids);
      const snapNodes = get().nodes.filter((n) => idSet.has(n.id));
      const snapEdges = get().edges.filter(
        (e) => idSet.has(e.source) || idSet.has(e.target));
      const label = cascade
        ? `${snapNodes.length} nodes`
        : (snapNodes[0]?.type ?? "node");
      try {
        await api.deleteNode(project.id, id, heal, cascade);
        set({ selected: null });
        await get().refreshGraph();
        get().refreshValidation();
        history.push({
          label: `delete ${label}`,
          // Restores the original ids, so Ren'Py labels come back unchanged.
          undo: async () => {
            await api.restoreNodes(project.id, snapNodes, snapEdges);
            await get().refreshGraph();
          },
          redo: async () => {
            await api.deleteNode(project.id, id, heal, cascade);
            await get().refreshGraph();
          },
        });
      } catch (e) {
        get().fail(String(e));
      }
    },

    persistPositions: async (positions) => {
      const project = get().project;
      if (!project || !positions.length) return;
      const before = positions.map((p) => {
        const node = get().nodes.find((n) => n.id === p.id);
        return { id: p.id, x: node?.x ?? p.x, y: node?.y ?? p.y };
      });
      set({
        nodes: get().nodes.map((n) => {
          const hit = positions.find((p) => p.id === n.id);
          return hit ? { ...n, x: hit.x, y: hit.y } : n;
        }),
      });
      await api.moveNodes(project.id, positions);
      history.push({
        label: positions.length > 1 ? "move nodes" : "move node",
        undo: async () => {
          await api.moveNodes(project.id, before);
          await get().refreshGraph();
        },
        redo: async () => {
          await api.moveNodes(project.id, positions);
          await get().refreshGraph();
        },
      });
    },

    connect: async (source, target, handle) => {
      const project = get().project;
      if (!project) return;
      // Connecting a port replaces whatever was there, so capture it first.
      const replaced = get().edges.find(
        (e) => e.source === source && e.source_handle === handle);
      try {
        const edge = await api.createEdge(project.id, {
          source, target, source_handle: handle,
        });
        await get().refreshGraph();
        get().refreshValidation();
        history.push({
          label: "connect",
          undo: async () => {
            await api.deleteEdge(project.id, edge.id);
            if (replaced) {
              await api.createEdge(project.id, {
                source: replaced.source, target: replaced.target,
                source_handle: replaced.source_handle,
              });
            }
            await get().refreshGraph();
          },
          redo: async () => {
            await api.createEdge(project.id, { source, target, source_handle: handle });
            await get().refreshGraph();
          },
        });
      } catch (e) {
        get().fail(String(e));
      }
    },

    disconnect: async (edgeId) => {
      const project = get().project;
      if (!project) return;
      const edge = get().edges.find((e) => e.id === edgeId);
      await api.deleteEdge(project.id, edgeId);
      await get().refreshGraph();
      get().refreshValidation();
      if (edge) {
        history.push({
          label: "disconnect",
          undo: async () => {
            await api.createEdge(project.id, {
              source: edge.source, target: edge.target, source_handle: edge.source_handle,
            });
            await get().refreshGraph();
          },
          redo: async () => {
            const current = get().edges.find(
              (e) => e.source === edge.source && e.source_handle === edge.source_handle);
            if (current) await api.deleteEdge(project.id, current.id);
            await get().refreshGraph();
          },
        });
      }
    },

    undo: async () => {
      try {
        const label = await history.undo();
        if (label) {
          get().notify(`undid ${label}`);
          get().refreshValidation();
        }
      } catch (e) {
        get().fail(`undo failed: ${e}`);
        await get().refreshGraph();
      }
    },

    redo: async () => {
      try {
        const label = await history.redo();
        if (label) {
          get().notify(`redid ${label}`);
          get().refreshValidation();
        }
      } catch (e) {
        get().fail(`redo failed: ${e}`);
        await get().refreshGraph();
      }
    },

    record: (label, undo, redo) => {
      history.push({ label, undo, redo });
    },

    upsertJob: (job) => {
      const jobs = get().jobs;
      const index = jobs.findIndex((j) => j.id === job.id);
      if (index < 0) {
        set({ jobs: [job, ...jobs] });
        return;
      }
      const current = jobs[index];
      if (current.status === job.status && current.label === job.label
          && current.progress === job.progress) return;
      const next = [...jobs];
      next[index] = job;
      set({ jobs: next });
    },

    select: (id) => set({ selected: id }),
    setView: (view) => set({ view }),

    // The three "take me to it" actions. Each one both selects and asks to be
    // shown, so the canvas centres the node and the inspector opens on it --
    // a plain select() only changes what is highlighted.
    focusNode: (id, view = "canvas") => set({
      selected: id, view, focusRequest: get().focusRequest + 1,
    }),
    focusCharacter: (id, tab) => set({
      selectedCharacterId: id, view: "characters",
      characterTab: tab ?? get().characterTab,
      focusRequest: get().focusRequest + 1,
    }),
    focusLocation: (id) => set({
      selectedLocationId: id, view: "setting",
      focusRequest: get().focusRequest + 1,
    }),

    selectCharacter: (id) => set({ selectedCharacterId: id }),
    selectLocation: (id) => set({ selectedLocationId: id }),
    setCharacterTab: (tab) => set({ characterTab: tab }),
    setShowOrphans: (value) => {
      set({ showOrphans: value });
      get().refreshGraph();
    },

    playFromNode: (nodeId, flags = {}) => {
      set({ playFrom: { nodeId, flags }, view: "preview" });
    },

    rememberSteerNote: (key, text) => {
      const next = { ...get().steerNotes };
      if (text.trim()) next[key] = text;
      else delete next[key];
      set({ steerNotes: next });
      try {
        localStorage.setItem("vn:steerNotes", JSON.stringify(next));
      } catch {
        /* private mode / quota — the note just won't persist across reloads */
      }
    },

    notify: (message) => {
      window.clearTimeout(toastTimer);
      set({ toast: message });
      toastTimer = window.setTimeout(() => set({ toast: null }), 4000);
    },

    fail: (message) => {
      window.clearTimeout(toastTimer);
      set({ toast: `⚠ ${message}` });
      toastTimer = window.setTimeout(() => set({ toast: null }), 8000);
    },

    subscribeEvents: (pid) => {
      eventSource?.close();
      const source = new EventSource(api.eventsUrl(pid));
      eventSource = source;
      let opened = false;
      // Everything that happened while the stream was down -- a server
      // restart, a laptop lid, a phone screen turning off -- is gone for good,
      // so a reconnect refetches rather than trusting what it last heard.
      const catchUp = () => {
        get().refreshJobs();
        get().refreshGraph();
        get().refreshValidation();
      };
      source.onopen = () => {
        if (opened) catchUp();
        opened = true;
      };
      source.onmessage = (event) => {
        let payload: any;
        try {
          payload = JSON.parse(event.data);
        } catch {
          return;
        }
        if (payload.type === "resync") {
          // The server dropped this tab's backlog rather than drop the tab.
          catchUp();
          return;
        }
        if (payload.type === "job.stream") {
          const streams: State["streams"] = { ...get().streams, [payload.id]: {
            text: String(payload.text ?? ""), done: !!payload.done } };
          // Only the recent few are ever looked at.
          const ids = Object.keys(streams);
          if (ids.length > 12) delete streams[ids[0]];
          set({ streams });
          return;
        }
        const jobs = get().jobs;
        const index = jobs.findIndex((j) => j.id === payload.id);
        if (payload.type === "job.progress" && index >= 0) {
          const next = [...jobs];
          next[index] = { ...next[index], progress: payload.progress,
                          label: payload.label, status: "running" };
          set({ jobs: next });
          return;
        }
        get().refreshJobs();
        if (["job.done", "job.error", "job.cancelled"].includes(payload.type)) {
          if (payload.type === "job.error") get().fail(`${payload.kind}: ${payload.error}`);
          if (payload.type === "job.done") {
            // Generated content arrives outside the undo journal; refetch it.
            get().refreshGraph();
            get().refreshCharacters();
            get().refreshValidation();
            get().refreshVariables();
            recordGeneration(payload.kind, payload.result);
          }
        }
      };
      source.onerror = () => {
        /* EventSource retries on its own */
      };
    },
  };
});

export const statusIcon: Record<string, string> = {
  none: "∅", queued: "⏳", running: "⚙", ready: "✓", stale: "⚠", error: "✗",
};

/** Nodes reachable *only* through `id` — the strict subtree. Mirrors the
 *  backend's Graph.strict_subtree so the "delete everything below" count, the
 *  undo snapshot, and what the server deletes all agree (item 12). */
export function descendantIds(nodes: IRNode[], edges: IREdge[], id: string): string[] {
  const candidates = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const cur = stack.pop()!;
    for (const e of edges) {
      if (e.source === cur && e.target !== id && !candidates.has(e.target)) {
        candidates.add(e.target);
        stack.push(e.target);
      }
    }
  }
  if (!candidates.size) return [];

  const keep = new Set<string>();
  const frontier: string[] = [];
  const reachInto = (from: string) => {
    for (const e of edges) {
      if (e.source === from && candidates.has(e.target) && !keep.has(e.target)) {
        keep.add(e.target);
        frontier.push(e.target);
      }
    }
  };
  for (const n of nodes) {
    if (n.id !== id && !candidates.has(n.id)) reachInto(n.id);
  }
  while (frontier.length) reachInto(frontier.pop()!);

  return [...candidates].filter((x) => !keep.has(x));
}

/**
 * Which scene the user is looking at, shared by Canvas and Script.
 *
 * The Canvas has persisted this to localStorage for a while; Script reads the
 * same key so switching views keeps you in the same scene instead of dumping
 * you back at the first one. The Canvas's `__no_scene__` sentinel (the
 * bucket for nodes outside any scene) has no meaning as a linearization start,
 * so it reads back as null.
 */
const SCENE_KEY = (projectId: string) => `vn:activeScene:${projectId}`;

export function readActiveScene(projectId: string): string | null {
  try {
    const saved = localStorage.getItem(SCENE_KEY(projectId));
    return saved && !saved.startsWith("__") ? saved : null;
  } catch {
    return null;            // private mode
  }
}

export function writeActiveScene(projectId: string, id: string | null): void {
  if (!id || id.startsWith("__")) return;
  try {
    localStorage.setItem(SCENE_KEY(projectId), id);
  } catch { /* private mode / quota */ }
}
