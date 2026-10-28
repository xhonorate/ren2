export type NodeType =
  | "scene" | "say" | "narration" | "stage" | "choice" | "hub" | "condition"
  | "set" | "jump" | "call" | "audio" | "transition" | "raw" | "end";

/** One structured gate on a hub option. `character` empty means `stat` is
 *  already a whole variable name rather than a per-character stat. */
export interface StatCondition {
  stat: string;
  character: string;
  op: "==" | "!=" | ">" | ">=" | "<" | "<=";
  value: string;
}

export interface HubOption {
  id: string;
  label: string;
  requires: StatCondition[];
  /** What an unmet option does: vanish, or show greyed as something to aim at. */
  locked: "hide" | "grey";
  locked_label: string;
  /** Stays on the menu however often it is taken, despite `exhaust`. */
  repeatable: boolean;
  branch_hint: string;
  effects: ChoiceEffect[];
  /** Empty = go wherever the port is wired. Otherwise the next scene in this
   *  route the player has not reached yet. */
  target_route: string;
}

export type AssetStatus = "none" | "queued" | "running" | "ready" | "stale" | "error";

export interface IRNode {
  id: string;
  type: NodeType;
  data: Record<string, any>;
  x: number;
  y: number;
  parent_id: string | null;
  pinned_label: boolean;
  slug: string;
  notes: string;
  text_hash: string;
  text_status: AssetStatus;
  image_status: AssetStatus;
  audio_status: AssetStatus;
  variants: Variant[];
  active_variant: string | null;
  orphaned: boolean;
}

export interface Variant {
  id: string;
  kind: "text" | "image" | "audio";
  payload: Record<string, any>;
  asset_hash: string | null;
  meta: {
    model?: string;
    params?: Record<string, any>;
    seed?: number | null;
    prompt_hash?: string | null;
    timestamp?: string | null;
  };
  created_at: string | null;
}

export interface IREdge {
  id: string;
  source: string;
  target: string;
  source_handle: string;
  label: string;
}

export interface TaggedField { label: string; tag: string; }

export interface Outfit {
  id: string; name: string; tags: string; negative: string;
  hidden_marks?: string[]; hidden_accessories?: string[];
}

export type VarType = "bool" | "int" | "str";

export interface VariableDef {
  name: string;
  type: VarType;
  default: string;
  note: string;
}

/** A variable name plus where it came from: declared, inferred from usage, or
 *  instantiated from the character-stat schema (`source: "stat"`), which is
 *  the only kind nobody ever typed. */
export interface VariableInfo extends VariableDef {
  derived: boolean;
  source?: "stat";
  stat_id?: string;
  character_id?: string;
  /** What a picker shows for a stat instance, e.g. "Diana — Affection". */
  label?: string;
}

export interface ChoiceEffect { var: string; op: string; value: string; }

/** One picked gate: `{stat}_{character} op value`. With no character the stat
 *  field is already a plain variable name. Hub topics, choice options and
 *  condition branches all gate this way, ANDed with any typed expression. */
export interface StatGate {
  stat: string;
  character: string;
  op: string;
  value: string;
}

/** A named range on a character stat. The label alone is enough for the writer
 *  ("Affection 34 (warming)"); `note` is opt-in flavour. */
export interface StatBand { min: number; label: string; note: string; }

/** Declared once on the project, instantiated per character as
 *  `{id}_{character_id}`. `in_card: false` keeps a content pointer (route
 *  progress) out of the writer's prompt while still compiling to a variable. */
export interface StatDef {
  id: string;
  name: string;
  type: VarType;
  default: string;
  min: number | null;
  max: number | null;
  note: string;
  bands: StatBand[];
  in_card: boolean;
}

export interface SheetPoolItem {
  hash: string;
  outfit_id: string;
  angle: string;
  expression: string;
  // CG-set only, empty on sheet images: what the body is doing and who else
  // is in frame. The sheet is deliberately one composition; the CG set is not.
  action: string;
  company: string;
  framing: string;
  scene: string;
  seed: number;
  caption: string;
}

export interface Character {
  id: string;
  display_name: string;
  age: number;
  gender: string;
  is_player: boolean;
  color: string;
  appearance: {
    hair_color: TaggedField; hair_style: TaggedField; eye_color: TaggedField;
    eye_quality: TaggedField; face: TaggedField; skin_tone: TaggedField; build: TaggedField;
    height: TaggedField; bust: TaggedField; butt: TaggedField;
    rest_expression: TaggedField; posture: TaggedField;
    accessories: TaggedField[]; distinguishing: TaggedField[]; negatives: TaggedField[];
  };
  outfits: Outfit[];
  default_outfit: string;
  personality: {
    summary: string; speech_style: string; speech_register: string;
    wants: string; fears: string; relationship_to_player: string;
    loves: string; likes: string; dislikes: string; background: string; hobbies: string;
    /** Rendered into the character card only for a scene flagged nsfw. */
    nsfw: string;
  };
  voice: {
    description: string; reference_clip: string | null; emotion_scale: number;
    voiced: boolean; pitch: number; speed: number;
    audition: AuditionTake[];
  };
  assets: {
    // Sprite LoRA -- trained on the character sheet, what sprites always load.
    lora: string | null; trigger: string; lora_weight: number;
    // CG LoRA -- the optional refinement trained on a varied dataset (shot
    // with the Sprite LoRA, cg_pool below). An event CG uses it, else the
    // Sprite LoRA, else none (cg_lora on the node / cg_lora_mode setting).
    lora_cg: string | null;
    cg_lora_weight: number; face_lora_weight: number;
    // Sprite LoRA's own bootstrap pool (image.sheet) and the CG LoRA's own
    // training pool (image.cg_set) -- culled, captioned and trained
    // separately, never mixed.
    sheet_pool: SheetPoolItem[]; cg_pool: SheetPoolItem[];
    sprite_sheet: Record<string, string>; negative: string;
    // A headshot for the character overview grid -- identity + one expression,
    // upper-body portrait. Authoring-tool only; never compiled into the game.
    profile_image: string | null;
  };
  /** Starting values for the project's stats, by stat id. Absent = the
   *  StatDef's own default, so only a character who differs needs an entry. */
  stat_overrides: Record<string, string>;
}

export interface AuditionTake {
  hash: string;
  preview_hash: string;
  emotion: string;
  intensity: number;
  line: string;
  ext: string;
}

export interface Location {
  id: string;
  name: string;
  tags: string;
  variants: Record<string, string>;
  negative: string;
}

export interface Project {
  id: string;
  name: string;
  world: {
    // No `rating`: the content register is per scene (SceneData.nsfw), because
    // one rating over a whole story cannot describe a story with a single
    // explicit scene in it.
    setting: string; era: string; pov: string; tense: string;
    style_notes: string; themes: string;
  };
  variables: VariableDef[];
  character_stats: StatDef[];
  style_tags: string;
  quality_prefix: string;
  global_negative: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface Job {
  id: string;
  project_id: string;
  kind: string;
  status: "queued" | "claimed" | "running" | "done" | "error" | "cancelled";
  progress: number;
  label: string;
  node_id: string | null;
  payload: Record<string, any>;
  result: Record<string, any>;
  error: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Problem {
  node_id: string;
  severity: "error" | "warning";
  message: string;
}

export interface Vocab {
  tags: Record<string, { label: string; tag: string }[]>;
  emotions: string[];
  expressions: string[];
  poses: string[];
  transitions: string[];
  shots: string[];
  cg_tags: Record<string, string[]>;
  cg_lora_modes: string[];
  cg_set_actions: string[];
  cg_set_company: string[];
  cg_set_framings: string[];
  time_of_day: string[];
  weather: string[];
  emotion_dims: string[];
  emotion_map: Record<string, any>;
}

export type PreviewOp = Record<string, any>;

export interface PreviewProgram {
  entry: string | null;
  blocks: Record<string, PreviewOp[]>;
  labels: Record<string, string>;
  characters: Record<string, { id: string; name: string; color: string; is_player: boolean; voiced: boolean }>;
  locations: Record<string, { id: string; name: string; variants: Record<string, string> }>;
  flags: Record<string, any>;
  /** route name -> its scene labels in order. The player marks each visited as
   *  it enters, which is how a hub option targeting a route knows what is next. */
  routes: Record<string, string[]>;
  warnings: string[];
  scenes: { id: string; label: string; title: string }[];
}


export interface ScriptRow {
  node_id: string | null;
  kind: string;
  text: string;
  speaker: string | null;
  speaker_name: string | null;
  speaker_color: string | null;
  expression: string;
  emotion: string;
  intensity: number;
  delivery: string;
  meta: string;
  label: string | null;
  editable: boolean;
  is_merge: boolean;
  options: {
    id: string; label: string; condition: string; branch_hint?: string;
    effects?: ChoiceEffect[];
    target: string | null; wired: boolean;
  }[];
  selected_option: string | null;
  statuses: Record<string, string>;
  has_voice: boolean;
  has_image: boolean;
  /** The CG on this row, so it can be shown as a thumbnail without a fetch
   *  per row to get the node. */
  image_hash: string | null;
  pose: string;
  sfx_cue: string;
  shot_hint: string;
  cg: boolean;
  /** Keep the CG up for the lines after this one. */
  cg_hold: boolean;
  /** Take a held CG down before this line. */
  cg_clear: boolean;
  notes: string;
  pinned_label: boolean;
  /** The generation this node came from — "regenerate this block" needs to
   *  know there is a block to regenerate. */
  gen_id: string | null;
  /** Author-facing directions the last generation offered from this node. */
  continuations: string[];
  variant_count: number;
}

// --- roleplay ------------------------------------------------------------
// A conversation with the cast, stored in its own tables. Nothing here is a
// node: the only bridge to the graph is the one-way port.

export interface RPPersona {
  name: string;
  description: string;
  notes: string;
}

export interface RPCharacter {
  id: string;
  name: string;
  color: string;
  profile_image: string | null;
  summary: string;
  /** Has a frozen reference clip, so their lines can actually be spoken. */
  voiced: boolean;
  conversations: number;
  last_at: string | null;
}

export interface RPTake {
  id: string;
  text: string;
  model: string;
  params: Record<string, unknown>;
  created_at: string | null;
}

export interface RPVoiceClip {
  /** Index of the dialogue line within the message. */
  line: number;
  character_id: string;
  hash: string;
  emotion: string;
  intensity: number;
}

export interface RPMessage {
  id: string;
  conv_id: string;
  seq: number;
  role: "player" | "cast";
  text: string;
  takes: RPTake[];
  active_take: string | null;
  voice: RPVoiceClip[];
  created_at: string | null;
}

export interface RPConversation {
  id: string;
  project_id: string;
  character_id: string;
  /** Everyone else in the scene; the primary character is not repeated. */
  cast: string[];
  title: string;
  /** True when `title` is derived rather than set, so the UI can italicise it. */
  untitled: boolean;
  scene_context: string;
  summary: string;
  summary_edited: boolean;
  state: "open" | "ended";
  hidden: boolean;
  sort_key: number;
  location_id: string | null;
  time_of_day: string | null;
  nsfw: boolean;
  created_at: string | null;
  updated_at: string | null;
  message_count: number | null;
  /** The scene this was ported into, or null. Read off the graph rather than
   *  stored here: delete the scene and the conversation is un-ported. */
  scene_id: string | null;
}

/** A conversation plus its messages. */
export interface RPThread extends RPConversation {
  messages: RPMessage[];
}

/** Exactly what the next reply would be sent. The tuning surface. */
export interface RPPreview {
  messages: { role: "system" | "user" | "assistant"; content: string }[];
  system: string;
  directive: string;
  cast: string[];
  cast_names: string;
  persona_name: string;
  silent: boolean;
  opening: boolean;
  steer: string;
  dropped_turns: number;
  token_estimate: number;
  over_budget: number;
}

export interface ScriptResponse {
  rows: ScriptRow[];
  taken: Record<string, string>;
  start: string | null;
  labels: Record<string, string>;
  scenes: { id: string; title: string }[];
}

export interface GalleryAsset {
  hash: string;
  kind: string;
  ext: string;
  meta: Record<string, any>;
  created_at: string;
  bytes: number;
  exists: boolean;
  /** Character this asset belongs to, when one can be resolved (sprite owner,
   *  say-node speaker, meta tag). null groups it under "Unattributed". */
  character_id: string | null;
  character_name: string | null;
  used_by: {
    kind: string; id: string; role: string; label: string;
    /** Present when this reference can be re-shot in place -- the backend
     *  names the job and carries its payload, so the gallery never has to
     *  work one out from the role string. */
    regen?: { action: RegenAction; ref: string; payload: Record<string, any> };
  }[];
  use_count: number;
  /** Semantic type -- what the asset *is*, as opposed to `kind`, which is only
   *  the folder its bytes live in. One of AssetType. */
  type: AssetType;
  /** Scenes this asset belongs to. A background staged by three scenes is in
   *  all three; an asset attached to nothing is in none. */
  scene_ids: string[];
  scene_names: string[];
}

/** What kind of generator re-shoots a reference. Mirrors
 *  `services/assets.REGEN_JOBS`. */
export type RegenAction = "cg" | "voice" | "sprite" | "background" | "portrait";

export interface RegenerateResult {
  queued: { ref: string; action: RegenAction; role: string; label: string;
            job_id: string }[];
  count: number;
  job_ids: string[];
}

export type AssetType =
  | "sprite" | "cg" | "background" | "portrait"
  | "voice" | "voice_ref" | "sfx" | "other";

export interface PurgeUnusedResult {
  deleted: string[];
  count: number;
  bytes: number;
  errors: { hash: string; error: string }[];
}

export interface GalleryResponse {
  /** This page only -- `offset`..`offset + limit` of the filtered set. */
  assets: GalleryAsset[];
  /** The whole filtered set, not the page. */
  total: number;
  bytes: number;
  unused: number;
  offset: number;
  limit: number;
  has_more: boolean;
  /** The store ignoring every filter -- what "delete unused" acts on. */
  library_total: number;
  library_unused: number;
  /** How many assets each filter option would match, counted with the other
   *  filters applied but not its own. */
  facets: {
    types: Record<string, number>;
    characters: Record<string, number>;
    scenes: Record<string, number>;
    used: { used: number; unused: number };
  };
  scenes: { id: string; title: string }[];
}

export interface GalleryQuery {
  types: string[];
  characters: string[];
  scenes: string[];
  used: "all" | "used" | "unused";
  offset: number;
  limit: number;
}

export interface MissingReport {
  voice: { node_id: string; speaker: string; reason: string; text: string }[];
  /** Sound cues with no file behind them. Never queued — nothing generates
   *  audio — so these are reported rather than filled. */
  sounds: {
    node_id: string; kind: "sfx" | "music"; cue: string; field: string;
    text: string; scene: string; scene_id: string | null; order: number;
  }[];
  cgs: {
    node_id: string; reason: string; text: string;
    /** Nothing authored to shoot from: no shot hint, no camera/action tags,
     *  and no pose that carries one. These are the CG director's work. */
    needs_direction: boolean;
  }[];
  /** The subset of `cgs` with `needs_direction` — queued as its own kind so a
   *  fill run can direct the shots before it renders them. */
  cg_prompts: MissingReport["cgs"];
  backgrounds: { location_id: string; variant: string; reason: string }[];
  sprites: {
    character_id: string; outfit_id: string; pose: string; expression: string;
    slot: string; reason: string;
  }[];
  blocked: string[];
  counts: {
    voice: number; cgs: number; backgrounds: number; sprites: number;
    /** Deliberately not part of `total`: `total` is what a fill run queues. */
    sounds: number;
    /** Also not part of `total`: a subset of `cgs`, not an asset of its own. */
    cg_prompts: number;
    total: number;
  };
  estimate_seconds: number;
}

/** One measured scene. Counted across every branch in it, not one path --
 *  the branch nobody walks still had to be written, and still gets voiced. */
export interface PacingScene {
  id: string;
  title: string;
  route: string;
  nsfw: boolean;
  words: number;
  lines: number;
  say_lines: number;
  narration_lines: number;
  choices: number;
  options: number;
  nodes: number;
  voiced: number;
  voiceable: number;
  cgs: number;
  cgs_done: number;
  backgrounds: number;
  backgrounds_done: number;
  /** Deepest stack of live decisions on any one path through the scene. */
  depth: number;
  speakers: PacingSpeaker[];
  seconds: number;
  /** null when nothing in it would ever be voiced -- not the same as 0%. */
  voiced_pct: number | null;
  imaged_pct: number | null;
  words_per_line: number;
  /** null when the scene has no choice at all: a 0 would sort as the
   *  tightest pacing in the story. */
  lines_per_choice: number | null;
}

export interface PacingSpeaker {
  id: string;
  name: string;
  color: string;
  lines: number;
  words: number;
  share_pct?: number;
}

export interface PacingRoute extends Omit<PacingScene,
  "id" | "title" | "route" | "nsfw" | "depth" | "speakers"> {
  /** "" is the trunk: scenes claiming no route, kept as a bucket of its own. */
  name: string;
  scenes: number;
  scenes_list: PacingScene[];
  cast: PacingSpeaker[];
  max_depth: number;
}

export interface PacingCharacter extends PacingSpeaker {
  is_player: boolean;
  scenes: number;
  routes: string[];
  voiced_pct: number | null;
  share_pct: number;
}

export interface PacingReport {
  routes: PacingRoute[];
  totals: PacingRoute;
  characters: PacingCharacter[];
  words_per_minute: number;
}
