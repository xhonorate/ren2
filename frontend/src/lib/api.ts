import { UnauthorizedError, reportUnauthorized } from "./auth";
import type {
  Character, GalleryQuery, GalleryResponse, IREdge, IRNode, Job,
  Location, MissingReport, PacingReport, PreviewProgram, Problem, Project, PurgeUnusedResult,
  RegenerateResult, RPCharacter, RPConversation, RPMessage, RPPersona, RPPreview, RPThread,
  ScriptResponse, VariableDef, VariableInfo, Vocab,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    // Same-origin by default; keeps the PIN cookie attached when one is set.
    credentials: "same-origin",
    ...init,
  });
  if (res.status === 401) {
    // Re-lock the PIN gate. Never reload here: the workspace fetches on mount,
    // so reloading on 401 would spin the page forever and the PIN form would
    // never render.
    reportUnauthorized();
    throw new UnauthorizedError();
  }
  if (!res.ok) {
    let detail = await res.text();
    try {
      detail = JSON.parse(detail).detail ?? detail;
    } catch {
      /* plain text body */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const get = <T,>(p: string) => request<T>(p);
const post = <T,>(p: string, body?: unknown) =>
  request<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const patch = <T,>(p: string, body: unknown) =>
  request<T>(p, { method: "PATCH", body: JSON.stringify(body) });
const put = <T,>(p: string, body: unknown) =>
  request<T>(p, { method: "PUT", body: JSON.stringify(body) });
const del = <T,>(p: string) => request<T>(p, { method: "DELETE" });

export const api = {
  // projects -------------------------------------------------------------
  listProjects: () => get<Project[]>("/projects"),
  createProject: (name: string) => post<Project>("/projects", { name }),
  getProject: (id: string) => get<Project>(`/projects/${id}`),
  updateProject: (id: string, body: Partial<Project>) => patch<Project>(`/projects/${id}`, body),
  deleteProject: (id: string) => del<{ deleted: boolean }>(`/projects/${id}`),
  listVariables: (id: string) => get<{ variables: VariableInfo[] }>(`/projects/${id}/variables`),
  saveVariables: (id: string, variables: VariableDef[]) =>
    put<{ variables: VariableDef[] }>(`/projects/${id}/variables`, { variables }),

  // settings + health ----------------------------------------------------
  getSettings: () => get<Record<string, any>>("/settings"),
  putSettings: (body: Record<string, any>) => put<Record<string, any>>("/settings", body),
  serviceHealth: () => get<any>("/health/services"),
  /** What LM Studio has loadable right now, for the model selects. Separate
   *  from serviceHealth so the Config page can fill them on mount without also
   *  sweeping ComfyUI. Never throws on an unreachable server. */
  llmModels: () => get<{ ok: boolean; models: string[]; error?: string }>("/llm/models"),
  freeVram: () => post<any>("/comfyui/free"),
  unloadLlm: () => post<{ unloaded: boolean; note: string }>("/llm/unload"),
  vramStatus: () => get<{
    exclusive: boolean;
    /** Engine ids the queue believes hold the card; null = not known yet. */
    resident: string[] | null;
    can_unload_llm: boolean;
    engines: Record<string, string>;
  }>("/vram"),
  interrupt: () => post<any>("/comfyui/interrupt"),
  vocab: () => get<Vocab>("/vocab"),
  /** Booru-tag autocomplete. Server-side because the corpus is ~30k rows
   *  after an import and the ranking wants the post counts. */
  /** `prefer` ranks those categories first without hiding anything else --
   *  classification is a pattern guess, so a hard filter would silently drop
   *  the tags it guessed wrong about. */
  tagSearch: (q: string, limit = 10, prefer?: string[]) =>
    get<{ results: { name: string; count: number; category: string; curated?: boolean }[] }>(
      `/tags/search?q=${encodeURIComponent(q)}&limit=${limit}`
      + (prefer?.length ? `&prefer=${encodeURIComponent(prefer.join(","))}` : "")),
  /** Which of these tags the dictionary does not know, and the likely fix.
   *  The screen director output already gets, for the record fields that
   *  reach ComfyUI verbatim and never had one. */
  tagCheck: (tags: string[]) =>
    get<{ unknown: { tag: string; suggestion: string }[] }>(
      `/tags/check?tags=${encodeURIComponent(tags.join(","))}`),
  tagStatus: () => get<{
    total: number; curated_only: boolean; curated: number;
    imported_at: string | null; min_count: number | null; path: string;
    categories: Record<string, number>;
  }>("/tags/status"),
  importTags: (minCount: number) =>
    post<{ job_id: string }>("/tags/import", { min_count: minCount }),

  // graph ----------------------------------------------------------------
  getGraph: (pid: string, includeOrphaned = false) =>
    get<{ nodes: IRNode[]; edges: IREdge[]; labels: Record<string, string>;
          scene_of: Record<string, string>; orphan_count: number;
          scenes: { id: string; title: string; label: string }[] }>(
      `/projects/${pid}/graph?include_orphaned=${includeOrphaned}`),
  createNode: (pid: string, body: any) => post<IRNode>(`/projects/${pid}/nodes`, body),
  updateNode: (pid: string, nid: string, body: any) =>
    patch<IRNode>(`/projects/${pid}/nodes/${nid}`, body),
  deleteNode: (pid: string, nid: string, heal = false, cascade = false) =>
    del<any>(`/projects/${pid}/nodes/${nid}?heal=${heal}&cascade=${cascade}`),
  insertAfter: (pid: string, body: any) =>
    post<{ node: IRNode; reattached_to: string | null }>(
      `/projects/${pid}/nodes/insert-after`, body),
  restoreNodes: (pid: string, nodes: IRNode[], edges: IREdge[]) =>
    post<any>(`/projects/${pid}/nodes/restore`, { nodes, edges }),
  moveNodes: (pid: string, positions: { id: string; x: number; y: number }[]) =>
    post<any>(`/projects/${pid}/nodes/positions`, { positions }),
  createEdge: (pid: string, body: any) => post<IREdge>(`/projects/${pid}/edges`, body),
  deleteEdge: (pid: string, eid: string) => del<any>(`/projects/${pid}/edges/${eid}`),
  pathState: (pid: string, nid: string) => get<any>(`/projects/${pid}/nodes/${nid}/state`),
  validate: (pid: string) =>
    get<{ problems: Problem[]; errors: number; warnings: number }>(`/projects/${pid}/validate`),
  search: (pid: string, q: string) =>
    get<any[]>(`/projects/${pid}/search?q=${encodeURIComponent(q)}`),
  orphans: (pid: string) => get<IRNode[]>(`/projects/${pid}/orphans`),
  restoreOrphans: (pid: string, ids: string[]) =>
    post<any>(`/projects/${pid}/orphans/restore`, { node_ids: ids }),
  purgeOrphans: (pid: string) => del<any>(`/projects/${pid}/orphans`),
  staleness: (pid: string) => get<any>(`/projects/${pid}/staleness`),
  pacing: (pid: string) => get<PacingReport>(`/projects/${pid}/pacing`),
  renameRoute: (pid: string, old: string, next: string) =>
    post<{ updated: number }>(`/projects/${pid}/routes/rename`, { old, new: next }),

  // characters + locations ----------------------------------------------
  listCharacters: (pid: string) => get<Character[]>(`/projects/${pid}/characters`),
  createCharacter: (pid: string, body: any) => post<Character>(`/projects/${pid}/characters`, body),
  saveCharacter: (pid: string, c: Character) =>
    put<Character>(`/projects/${pid}/characters/${c.id}`, c),
  deleteCharacter: (pid: string, cid: string) => del<any>(`/projects/${pid}/characters/${cid}`),
  promptPreview: (pid: string, cid: string, outfit: string, expression: string) =>
    get<any>(`/projects/${pid}/characters/${cid}/prompt-preview?outfit_id=${outfit}&expression=${expression}`),
  emotionPreview: (pid: string, cid: string, emotion: string, intensity: number) =>
    get<any>(`/projects/${pid}/characters/${cid}/emotion-preview?emotion=${emotion}&intensity=${intensity}`),
  adoptClip: (pid: string, cid: string, hash: string, ext: string) =>
    post<Character>(`/projects/${pid}/characters/${cid}/voice-clip/adopt`, { hash, ext }),
  uploadVoiceClip: async (pid: string, cid: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${BASE}/projects/${pid}/characters/${cid}/voice-clip`, {
      method: "POST", body: form,
    });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  listLocations: (pid: string) => get<Location[]>(`/projects/${pid}/locations`),
  createLocation: (pid: string, body: any) => post<Location>(`/projects/${pid}/locations`, body),
  saveLocation: (pid: string, l: Location) =>
    put<Location>(`/projects/${pid}/locations/${l.id}`, l),
  deleteLocation: (pid: string, lid: string) => del<any>(`/projects/${pid}/locations/${lid}`),

  // generation -----------------------------------------------------------
  generateScene: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/generate/scene`, body),
  generateBranch: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/generate/branch`, body),
  regenerateFrom: (pid: string, nodeId: string, brief = "", instruction = "",
                   continuation = "") =>
    post<{ job_id: string }>(`/projects/${pid}/generate/regenerate-from`,
      { node_id: nodeId, brief, instruction, continuation }),
  /** Redo the whole generation this node belongs to, from wherever it started
   *  — not from the node you happen to have selected. */
  regenerateBlock: (pid: string, nodeId: string, instruction = "") =>
    post<{ job_id: string; head: string }>(`/projects/${pid}/generate/regenerate-block`,
      { node_id: nodeId, instruction }),
  /** Rewind: park everything downstream of a node. Reversible — the nodes go
   *  to the holding area, not the bin, and come back via restoreOrphans. */
  parkAfter: (pid: string, nodeId: string, includeSelf = false) =>
    post<{ parked: string[]; count: number }>(`/projects/${pid}/generate/park-after`,
      { node_id: nodeId, include_self: includeSelf }),
  rollbackGeneration: (pid: string, desc: Record<string, any>) =>
    post<any>(`/projects/${pid}/generate/rollback`, desc),
  reapplyGeneration: (pid: string, desc: Record<string, any>) =>
    post<any>(`/projects/${pid}/generate/reapply`, desc),
  contextPreview: (pid: string, nodeId: string, brief = "", instruction = "",
                   continuation = "", branchHint = "") =>
    get<any>(`/projects/${pid}/context-preview?node_id=${nodeId}`
      + `&brief=${encodeURIComponent(brief)}`
      + `&instruction=${encodeURIComponent(instruction)}`
      + `&continuation=${encodeURIComponent(continuation)}`
      + `&branch_hint=${encodeURIComponent(branchHint)}`),
  variants: (pid: string, nid: string) => get<any>(`/projects/${pid}/nodes/${nid}/variants`),
  applyVariant: (pid: string, nid: string, vid: string) =>
    post<IRNode>(`/projects/${pid}/nodes/${nid}/variants/${vid}/apply`),

  // media ----------------------------------------------------------------
  generateCG: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/images/cg`, body),
  /** Re-sample an existing take rather than rolling a new one from noise.
   *  `mask` is a black-and-white PNG data URL; white is the region to
   *  regenerate, absent means the whole frame. */
  refineCG: (pid: string, body: {
    node_id: string; source_hash?: string; denoise?: number;
    mask?: string; batch?: number; seed?: number | null;
  }) => post<{ job_id: string }>(`/projects/${pid}/images/refine`, body),
  distillCgPrompt: (pid: string, nodeId: string, instruction = "") =>
    post<{ job_id: string }>(`/projects/${pid}/images/cg/prompt`,
      { node_id: nodeId, instruction }),
  cgPromptPreview: (pid: string, nodeId: string) =>
    get<{
      positive: string; negative: string; subjects: string[];
      lora: string; lora_weight: number;
      face_positive: string; face_lora: string; face_lora_weight: number;
      /** Every tag the cast contributes to this shot, per character, before
       *  the node's own switches are applied — what the tag switchboard
       *  renders. Built by the assembly itself, so it never drifts. */
      cast: { character_id: string; name: string;
              identity: string[]; outfit: string[];
              /** Tags this framing removed by itself — a close-up does not
               *  contain shoes, so asking for them is asking for a pair of
               *  feet in the corner. */
              out_of_frame: string[] }[];
      hidden_tags: string[];
    }>(`/projects/${pid}/images/cg/preview?node_id=${nodeId}`),
  generateBackground: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/images/background`, body),
  generateSprite: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/images/sprite`, body),
  generateProfileImage: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/images/profile`, body),
  generateSpriteSet: (pid: string, body: any) =>
    post<{ job_ids: string[]; count: number }>(`/projects/${pid}/images/sprite-set`, body),
  generateSheet: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/images/sheet`, body),
  generateCgSet: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/images/cg-set`, body),
  captionImages: (pid: string, characterId: string, hashes: string[], pool: "sheet" | "cg" = "sheet") =>
    post<{ job_id: string }>(`/projects/${pid}/images/caption`, {
      character_id: characterId,
      hashes,
      pool,
    }),
  trainLora: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/lora/train`, body),
  /** `takes` > 1 is a re-take: that many fresh seeds, all kept to pick from. */
  voiceLine: (pid: string, nodeId: string, takes = 1) =>
    post<{ job_id: string }>(`/projects/${pid}/tts/line`, { node_id: nodeId, seed: 0, takes }),
  voiceAll: (pid: string, onlyMissing = true) =>
    post<{ job_ids: string[]; count: number }>(`/projects/${pid}/tts/all?only_missing=${onlyMissing}`),
  castVoice: (pid: string, body: any) =>
    post<{ job_id: string }>(`/projects/${pid}/tts/cast`, body),
  regenerateStale: (pid: string, kinds?: string[]) =>
    post<{ job_id: string }>(`/projects/${pid}/batch/regenerate-stale`, kinds ? { kinds } : {}),

  // build ----------------------------------------------------------------
  compile: (pid: string) =>
    get<{ files: Record<string, string>; warnings: string[]; labels: Record<string, string>;
          stats: Record<string, number>; assets: any[] }>(`/projects/${pid}/compile`),
  preview: (pid: string, fromNode?: string) =>
    get<PreviewProgram>(`/projects/${pid}/preview${fromNode ? `?from_node=${fromNode}` : ""}`),
  exportProject: (pid: string, body: any = {}) =>
    post<{ job_id: string }>(`/projects/${pid}/export`, body),
  launch: (pid: string) => post<any>(`/projects/${pid}/launch`),
  lint: (pid: string) => post<{ job_id: string }>(`/projects/${pid}/lint`),
  webBuild: (pid: string, body: any = {}) =>
    post<{ job_id: string }>(`/projects/${pid}/web-build`, body),
  openRenpyLauncher: () => post<any>("/renpy-launcher", {}),
  playUrl: (pid: string) => `${window.location.origin}/play/${pid}/`,
  buildInfo: (pid: string) => get<any>(`/projects/${pid}/build-info`),

  // jobs -----------------------------------------------------------------
  listJobs: (pid: string) => get<Job[]>(`/projects/${pid}/jobs`),
  getJob: (jid: string) => get<Job>(`/jobs/${jid}`),
  cancelJob: (jid: string) => post<any>(`/jobs/${jid}/cancel`),
  clearJobs: (pid: string) => del<any>(`/projects/${pid}/jobs`),

  // config ---------------------------------------------------------------
  listTemplates: (pid: string) => get<any>(`/projects/${pid}/templates`),
  getTemplate: (pid: string, key: string) => get<any>(`/projects/${pid}/templates/${key}`),
  saveTemplate: (pid: string, key: string, body: string, params: Record<string, any>) =>
    post<any>(`/projects/${pid}/templates/${key}`, { body, params }),
  diffTemplate: (pid: string, key: string, a: number, b: number) =>
    get<{ diff: string[] }>(`/projects/${pid}/templates/${key}/diff?a=${a}&b=${b}`),
  activateTemplate: (pid: string, key: string, tid: string) =>
    post<any>(`/projects/${pid}/templates/${key}/activate/${tid}`),
  resetTemplate: (pid: string, key: string) =>
    post<any>(`/projects/${pid}/templates/${key}/reset`),
  getWorkflows: () => get<any>("/workflows"),
  putWorkflow: (name: string, body: any) => put<any>(`/workflows/${name}`, body),
  resetWorkflow: (name: string) => del<any>(`/workflows/${name}`),
  putNodeClasses: (body: Record<string, string>) => put<any>("/node-classes", body),
  getEmotionMap: () => get<any>("/emotion-map"),
  putEmotionMap: (body: any) => put<any>("/emotion-map", body),

  // roleplay -------------------------------------------------------------
  // Conversation ids and message ids only. Nothing here takes or returns a
  // node id except `rpPort`, which is the explicit one-way copy into the graph.
  rpCharacters: (pid: string) =>
    get<{ characters: RPCharacter[]; persona: RPPersona;
          player: { id: string; name: string } | null }>(`/projects/${pid}/rp/characters`),
  rpConversations: (pid: string, characterId = "") =>
    get<{ conversations: RPConversation[] }>(
      `/projects/${pid}/rp/conversations${characterId ? `?character_id=${characterId}` : ""}`),
  rpCreateConversation: (pid: string, body: {
    character_id: string; cast?: string[]; scene_context?: string; title?: string;
    nsfw?: boolean;
  }) => post<RPConversation>(`/projects/${pid}/rp/conversations`, body),
  rpThread: (pid: string, cid: string) =>
    get<RPThread>(`/projects/${pid}/rp/conversations/${cid}`),
  rpPatchConversation: (pid: string, cid: string, body: Partial<RPConversation>) =>
    patch<RPConversation>(`/projects/${pid}/rp/conversations/${cid}`, body),
  rpDeleteConversation: (pid: string, cid: string) =>
    del<{ deleted: boolean }>(`/projects/${pid}/rp/conversations/${cid}`),
  rpReorder: (pid: string, ids: string[]) =>
    post<{ reordered: number }>(`/projects/${pid}/rp/conversations/reorder`, { ids }),
  /** Empty `text` means "carry on without me" — no player message is written. */
  rpTurn: (pid: string, cid: string, text: string) =>
    post<{ job_id: string; message: RPMessage | null }>(
      `/projects/${pid}/rp/conversations/${cid}/turn`, { text }),
  /** Another take on a reply. The one showing is kept as a swipe. */
  rpRegenerate: (pid: string, cid: string, messageId = "") =>
    post<{ job_id: string; message_id: string; dropped: string[] }>(
      `/projects/${pid}/rp/conversations/${cid}/regenerate`, { message_id: messageId }),
  rpPatchMessage: (pid: string, mid: string, body: { text?: string; active_take?: string }) =>
    patch<RPMessage>(`/projects/${pid}/rp/messages/${mid}`, body),
  /** `rewind` also drops everything after this message. */
  rpDeleteMessage: (pid: string, mid: string, rewind = false) =>
    del<{ deleted: string[] }>(`/projects/${pid}/rp/messages/${mid}?rewind=${rewind}`),
  rpEnd: (pid: string, cid: string, force = false) =>
    post<{ job_id: string }>(`/projects/${pid}/rp/conversations/${cid}/end?force=${force}`),
  /** The one-way copy into the graph. Returns a job; its result carries the
   *  new scene's node id. `polish` runs the optional `rp.to_scene` tidy-up
   *  first -- off by default, because the transcript is already house format
   *  and every extra pass is a chance to lose a line. */
  rpPort: (pid: string, cid: string, polish = false) =>
    post<{ job_id: string }>(
      `/projects/${pid}/rp/conversations/${cid}/port`, { polish }),
  rpSeed: (pid: string, characterId: string, count = 3) =>
    post<{ job_id: string }>(
      `/projects/${pid}/rp/seed?character_id=${characterId}&count=${count}`),
  /** Exactly what the next reply would be sent. The prompt-tuning surface. */
  rpPreview: (pid: string, cid: string) =>
    get<RPPreview>(`/projects/${pid}/rp/conversations/${cid}/preview`),
  /** Whether real-time voice fits alongside whatever the LLM is holding, read
   *  live off the cards. Advisory: the answer changes with the context length
   *  the model was loaded at. */
  rpVram: (pid: string) => get<{
    known: boolean;
    cards?: { index: number; name: string; total_mb: number; used_mb: number;
              free_mb: number }[];
    needed_mb: number;
    free_total_mb?: number;
    free_largest_card_mb?: number;
    fits?: boolean;
    note: string;
    tts_enabled: boolean;
    coresident: boolean;
  }>(`/projects/${pid}/rp/vram`),
  rpPersona: (pid: string) =>
    get<{ persona: RPPersona; resolved: RPPersona }>(`/projects/${pid}/rp/persona`),
  rpSavePersona: (pid: string, body: RPPersona) =>
    put<{ persona: RPPersona; resolved: RPPersona }>(`/projects/${pid}/rp/persona`, body),

  // script view ---------------------------------------------------------
  script: (pid: string, body: { start_id?: string | null; choices?: Record<string, string> }) =>
    post<ScriptResponse>(`/projects/${pid}/script`, body),
  scriptExport: (pid: string, body: { start_id?: string | null; choices?: Record<string, string> }) =>
    post<{ text: string }>(`/projects/${pid}/script/export`, body),
  scriptParse: (pid: string, text: string) =>
    post<{ nodes: any[]; warnings: string[]; count: number }>(
      `/projects/${pid}/script/parse`, { text }),
  scriptImport: (pid: string, body: { text: string; anchor_id: string; handle?: string }) =>
    post<{ created: IRNode[]; count: number; warnings: string[]; reattached_to: string | null }>(
      `/projects/${pid}/script/import`, body),

  // gallery + batch ------------------------------------------------------
  gallery: (pid: string, q: GalleryQuery) =>
    get<GalleryResponse>(`/projects/${pid}/gallery?` + new URLSearchParams({
      types: q.types.join(","),
      characters: q.characters.join(","),
      scenes: q.scenes.join(","),
      used: q.used,
      offset: String(q.offset),
      limit: String(q.limit),
    })),
  /** Deleting also unsets every reference to the file, so `cleared` is what
   *  the caller has to reload: nodes, characters and locations changed. */
  deleteGalleryAsset: (pid: string, hash: string, force = false) =>
    del<{
      deleted: boolean; hash: string; cleared_count: number;
      cleared: { kind: string; project_id: string; id: string; role: string }[];
    }>(`/projects/${pid}/gallery/${hash}?force=${force}`),
  /** Re-shoot an asset in place. `ref` picks one use of it (a `regen.ref` from
   *  `used_by`); left out, every regenerable use is redone. The job writes the
   *  new hash onto whatever pointed at the old one. */
  regenerateAsset: (pid: string, hash: string, ref = "") =>
    post<RegenerateResult>(`/projects/${pid}/gallery/${hash}/regenerate`, { ref }),
  deleteUnusedAssets: (pid: string) =>
    del<PurgeUnusedResult>(`/projects/${pid}/gallery-unused`),
  missingReport: (pid: string, includeStale = true) =>
    get<MissingReport>(`/projects/${pid}/batch/missing?include_stale=${includeStale}`),
  fillMissing: (pid: string, body: { kinds: string[]; include_stale: boolean; limit: number }) =>
    post<{ job_id: string }>(`/projects/${pid}/batch/fill-missing`, body),

  assetUrl: (hash: string) => `${BASE}/assets/${hash}`,
  eventsUrl: (pid: string) => `${BASE}/projects/${pid}/events`,
};
