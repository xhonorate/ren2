import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import TagInput from "../components/TagInput";
import ConfirmDialog from "../components/ConfirmDialog";
import { Area, Check, Num, Range, Select, Text } from "../components/Inspector";
import { useStore, type CharacterTab } from "../lib/store";
import { useIsNarrow } from "../lib/useMediaQuery";
import type { Character, SheetPoolItem, TaggedField } from "../lib/types";

export default function CharactersView() {
  const store = useStore();
  // Selection lives in the store (not local state) so it, and the sub-tab
  // below, survive navigating to another top-level view and back -- this
  // component fully unmounts on every tab switch.
  const selected = store.selectedCharacterId;
  const [draft, setDraft] = useState<Character | null>(null);
  const [creating, setCreating] = useState(false);
  // Phones stack the split and lose the room for a persistent roster column,
  // so the list collapses to a plain <select> (and the sub-tab strip does the
  // same, in CharacterEditor).
  const narrow = useIsNarrow();

  useEffect(() => {
    const character = store.characters.find((c) => c.id === selected);
    setDraft(character ? structuredClone(character) : null);
  }, [selected, store.characters]);

  if (!store.project) return null;

  const save = async (next: Character) => {
    setDraft(next);
    try {
      await api.saveCharacter(store.project!.id, next);
      await store.refreshCharacters();
    } catch (e) {
      store.fail(String(e));
    }
  };

  return (
    <div className="view">
      <h2>Characters</h2>
      <p className="lede">
        Every appearance field stores a human label and a booru tag. The writer
        model sees personality plus an appearance line composed from the
        dropdowns; the image model sees tags and the LoRA trigger, never
        personality prose.
      </p>

      <div className="split">
        <div className="left">
          {narrow ? (
            <div className="field">
              <label>Character</label>
              <select
                value={selected ?? ""}
                onChange={(e) => store.selectCharacter(e.target.value || null)}
              >
                <option value="">Overview — all characters</option>
                {store.characters.map((character) => (
                  <option key={character.id} value={character.id}>
                    {character.display_name}
                    {character.is_player ? " · PC" : ` · ${character.age}`}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="list">
              <div
                className={`item ${selected === null ? "active" : ""}`}
                onClick={() => store.selectCharacter(null)}
              >
                <span>▦</span>
                <span>Overview</span>
                <span className="sub">{store.characters.length}</span>
              </div>
              {store.characters.map((character) => (
                <div
                  key={character.id}
                  className={`item ${selected === character.id ? "active" : ""}`}
                  onClick={() => store.selectCharacter(character.id)}
                >
                  <span style={{ color: character.color }}>●</span>
                  <span>{character.display_name}</span>
                  <span className="sub">
                    {character.is_player ? "PC" : character.age}
                  </span>
                </div>
              ))}
            </div>
          )}
          <button
            className="primary"
            style={{ width: "100%", marginTop: 8 }}
            onClick={() => setCreating(true)}
          >
            + New character
          </button>
        </div>

        <div className="right">
          {!draft && (
            <CharacterOverview
              onSelect={(id) => store.selectCharacter(id)}
              onCreate={() => setCreating(true)}
            />
          )}
          {draft && (
            <CharacterEditor
              draft={draft}
              save={save}
              onDelete={async () => {
                await api.deleteCharacter(store.project!.id, draft.id);
                store.selectCharacter(null);
                store.refreshCharacters();
              }}
            />
          )}
        </div>
      </div>

      {creating && (
        <CreateModal
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            store.selectCharacter(id);
          }}
        />
      )}
    </div>
  );
}

// The default right-hand panel when no character is selected: a card per
// character showing everything you would otherwise have to open the editor to
// see -- headshot, name in their dialogue colour, the LoRA / voiced pills, a
// PC badge. Clicking a card opens that character; the × deletes it (confirmed).
function CharacterOverview({
  onSelect,
  onCreate,
}: {
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  const store = useStore();
  const [confirmDelete, setConfirmDelete] = useState<Character | null>(null);

  const remove = async (character: Character) => {
    try {
      await api.deleteCharacter(store.project!.id, character.id);
      await store.refreshCharacters();
    } catch (e) {
      store.fail(String(e));
    }
  };

  if (store.characters.length === 0) {
    return (
      <div className="card">
        <p className="hint">
          No characters yet. Create one to start building the cast.
        </p>
        <button className="primary" onClick={onCreate}>
          + New character
        </button>
      </div>
    );
  }

  return (
    <>
      <div className="char-grid">
        {store.characters.map((character) => {
          return (
            <div
              key={character.id}
              className="char-card"
              onClick={() => onSelect(character.id)}
            >
              <button
                className="char-card-del"
                title="Delete character"
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmDelete(character);
                }}
              >
                ×
              </button>
              <div className="char-card-portrait">
                {character.assets.profile_image ? (
                  <img
                    src={api.assetUrl(character.assets.profile_image)}
                    loading="lazy"
                    alt={character.display_name}
                  />
                ) : (
                  <span
                    className="char-card-noportrait"
                    style={{ color: character.color }}
                  >
                    ●
                  </span>
                )}
              </div>
              <div className="char-card-body">
                <div
                  className="char-card-name"
                  style={{ color: character.color }}
                >
                  {character.display_name}
                  {character.is_player ? (
                    <span className="pill">PC</span>
                  ) : (
                    <span className="pill">{character.id}</span>
                  )}
                </div>
                <div className="char-card-meta">
                  {character.age} · {character.gender}
                </div>
                <div className="char-card-pills">
                  {character.assets.lora && (
                    <span className="pill good">Sprite LoRA</span>
                  )}
                  {character.assets.lora_cg && (
                    <span className="pill good">CG LoRA</span>
                  )}
                  {character.voice.reference_clip && (
                    <span className="pill purple">voiced</span>
                  )}
                  {character.age < 18 && (
                    <span className="pill bad">under 18</span>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {confirmDelete && (
        <ConfirmDialog
          title={`Delete ${confirmDelete.display_name}?`}
          confirmLabel="Delete character"
          onCancel={() => setConfirmDelete(null)}
          onConfirm={() => {
            const target = confirmDelete;
            setConfirmDelete(null);
            remove(target);
          }}
        >
          <div className="hint">
            Removes the character record, its LoRAs' file references, and every
            generated sprite and pool image reference. Nodes that cast them keep
            the id, which will read as unknown until reassigned.
          </div>
        </ConfirmDialog>
      )}
    </>
  );
}

// One card, one collapsible section -- the unit every card on the character
// tab is built from, so long tabs (Voice, Sprites) can be collapsed down to
// just their titles on a phone instead of forcing a long scroll. Defaults
// open so nothing looks missing on first load; state resets per session, not
// per character, same as the tab selector itself.
function CollapsibleCard({
  title,
  headExtra,
  defaultOpen = true,
  children,
}: {
  title: string;
  headExtra?: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="card">
      <div className="card-toggle-row">
        <span className="card-toggle" onClick={() => setOpen(!open)}>
          <span className="chevron">{open ? "▾" : "▸"}</span>
          {title}
        </span>
        <span style={{ marginLeft: "auto" }} />
        {headExtra}
      </div>
      {open && children}
    </div>
  );
}

function CreateModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const store = useStore();
  const [name, setName] = useState("");
  const [age, setAge] = useState(21);
  const [gender, setGender] = useState("female");
  const [isPlayer, setIsPlayer] = useState(false);
  const [error, setError] = useState("");

  const create = async () => {
    try {
      const character = await api.createCharacter(store.project!.id, {
        display_name: name,
        age,
        gender,
        is_player: isPlayer,
      });
      await store.refreshCharacters();
      onCreated(character.id);
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        style={{ maxWidth: 420 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="head">
          <span className="title">New character</span>
        </div>
        <div className="content">
          <Text label="Display name" value={name} onChange={setName} />
          <div className="grid2">
            <Num label="Age" value={age} step={1} onChange={setAge} />
            <Select
              label="Gender"
              value={gender}
              options={[
                ["female", "female"],
                ["male", "male"],
                ["nonbinary", "nonbinary"],
              ]}
              onChange={setGender}
            />
          </div>
          <Check
            label="This is the player character"
            checked={isPlayer}
            onChange={setIsPlayer}
          />
          <div className="hint">
            Age is a hard 18+ floor enforced in the schema. A character record
            cannot reach the image pipeline without it.
          </div>
          {error && (
            <div className="hint bad" style={{ marginTop: 8 }}>
              {error}
            </div>
          )}
        </div>
        <div className="foot">
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!name.trim()} onClick={create}>
            Create
          </button>
        </div>
      </div>
    </div>
  );
}

function CharacterEditor({
  draft,
  save,
  onDelete,
}: {
  draft: Character;
  save: (c: Character) => void;
  onDelete: () => void;
}) {
  const store = useStore();
  // Kept in the store, not local state: the Characters view unmounts on
  // every top-level tab switch, and this is what makes the sub-tab you were
  // on survive navigating away and back.
  const tab = store.characterTab;
  const setTab = store.setCharacterTab;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const narrow = useIsNarrow();
  // The CG LoRA tab is trained *with* the Sprite LoRA (image.cg_set shoots
  // its dataset using it), so there is nothing for it to do until that
  // exists -- it appears once the Sprite LoRA has been trained rather than
  // showing an empty, permanently-disabled tab from the start.
  const tabs = [
    "general",
    "appearance",
    "outfits",
    "personality",
    "voice",
    "sprite-lora",
    "sprites",
    "cg-lora",
  ] as const;

  return (
    <>
      {narrow ? (
        <div className="field">
          <label>Section</label>
          <select
            value={tab}
            onChange={(e) => setTab(e.target.value as CharacterTab)}
          >
            {tabs.map((t) => (
              <option key={t} value={t}>
                {tabLabel(t)}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <div
          className="tabs"
          style={{ width: "fit-content", marginBottom: 10 }}
        >
          {tabs.map((t) => (
            <button
              key={t}
              className={tab === t ? "active" : ""}
              onClick={() => setTab(t)}
            >
              {tabLabel(t)}
            </button>
          ))}
        </div>
      )}

      {tab === "general" && <GeneralTab draft={draft} save={save} />}
      {tab === "appearance" && <AppearanceTab draft={draft} save={save} />}
      {tab === "outfits" && <OutfitsTab draft={draft} save={save} />}
      {tab === "personality" && <PersonalityTab draft={draft} save={save} />}
      {tab === "voice" && <VoiceTab draft={draft} save={save} />}
      {tab === "sprite-lora" && <SpriteLoraTab draft={draft} save={save} />}
      {tab === "sprites" && <SpritesTab draft={draft} save={save} />}
      {tab === "cg-lora" && <CgLoraTab draft={draft} save={save} />}
    </>
  );
}

// Human-readable label for a sub-tab id -- shared by the button strip and the
// mobile <select> that replaces it on a phone.
function tabLabel(t: CharacterTab): string {
  if (t === "cg-lora") return "CG LoRA";
  if (t === "sprite-lora") return "Sprite LoRA";
  return t.slice(0, 1).toUpperCase() + t.slice(1);
}

// The character's core record -- name, age, dialogue colour, gender, PC flag,
// plus the overview headshot. Its own tab (first, before Appearance) so the
// identity header card above the tab strip stays a compact at-a-glance summary
// rather than a form.
function GeneralTab({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  const set = (patch: Partial<Character>) => save({ ...draft, ...patch });
  return (
    <>
      <CollapsibleCard title="General">
        <div className="grid2" style={{ gap: 24 }}>
          <div>
            <Text
              label="Display name"
              value={draft.display_name}
              onChange={(v) => set({ display_name: v })}
            />
            <div className="field">
              <label>Dialogue colour</label>
              <input
                type="color"
                value={draft.color}
                onChange={(e) => set({ color: e.target.value })}
              />
            </div>
            <Num
              label="Age"
              step={1}
              value={draft.age}
              onChange={(v) => set({ age: v })}
            />
            <Select
              label="Gender"
              value={draft.gender || "female"}
              options={[
                ["female", "female"],
                ["male", "male"],
                ["nonbinary", "nonbinary"],
              ]}
              onChange={(v) => set({ gender: v })}
            />
            <div className="hint">
              Gender sets the <code>1girl</code>/<code>1boy</code> identity tag
              and is shown to the writer model. A player character is never
              voiced and gets no sprite unless it has outfits. Age is a hard 18+
              floor — the warning shows in the header card above and blocks the
              image pipeline.
            </div>
            <Check
              label="Player character"
              checked={draft.is_player}
              onChange={(v) => set({ is_player: v })}
            />
          </div>
          <div>
            <ProfilePictureCard draft={draft} save={save} />
          </div>
        </div>
      </CollapsibleCard>
    </>
  );
}

// The character-overview headshot: identity tags plus one expression, rendered
// as an upper-body portrait (backend: image.profile / assemble_portrait_prompt).
// It uses the CG LoRA if the character has one, else the Sprite LoRA, else no
// LoRA -- so it works from the moment a character has an appearance, and gets
// sharper once a LoRA is trained. Never compiled into the game; it only shows
// on the Overview grid and the header. Delete/regenerate mirror the sprite grid:
// save the character without the hash, then best-effort purge the file.
function ProfilePictureCard({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  const store = useStore();
  const [expression, setExpression] = useState("neutral");
  const [busy, setBusy] = useState(false);
  const hash = draft.assets.profile_image;

  const generate = async () => {
    try {
      setBusy(true);
      const { job_id } = await api.generateProfileImage(store.project!.id, {
        character_id: draft.id,
        expression,
      });
      store.notify("rendering profile picture…");
      store.refreshJobs();
      const poll = setInterval(async () => {
        const job = await api.getJob(job_id);
        if (job.status === "done") {
          clearInterval(poll);
          setBusy(false);
          await store.refreshCharacters();
          store.notify("profile picture ready");
        } else if (["error", "cancelled"].includes(job.status)) {
          clearInterval(poll);
          setBusy(false);
          if (job.error) store.fail(job.error.split("\n")[0]);
        }
      }, 2000);
    } catch (e) {
      setBusy(false);
      store.fail(String(e));
    }
  };

  const remove = async () => {
    if (!hash) return;
    await save({
      ...draft,
      assets: { ...draft.assets, profile_image: null },
    });
    // The backend refuses to delete anything the character still references, so
    // this only runs after the save above has landed -- same contract as the
    // sprite / pool cull.
    api.deleteGalleryAsset(store.project!.id, hash).catch(() => {});
  };

  return (
    <>
      <div className="section-title" style={{ marginBottom: 8 }}>
        Profile Image
      </div>
      {hash && (
        <img
          src={api.assetUrl(hash)}
          style={{
            maxWidth: 220,
            marginTop: 10,
            borderRadius: 8,
            border: "2px solid var(--line)",
            opacity: busy ? 0.5 : 1,
          }}
        />
      )}
      <div className="row" style={{ alignItems: "flex-center", gap: 8 }}>
        <div style={{ flex: 1 }}>
          <Select
            label="Expression"
            value={expression}
            options={(store.vocab?.expressions ?? []).map(
              (e) => [e, e] as [string, string],
            )}
            onChange={setExpression}
          />
        </div>
        <button
          style={{ marginTop: 10 }}
          className="primary"
          disabled={busy}
          title={"Render this character's headshot through ComfyUI"}
          onClick={generate}
        >
          {busy ? "Rendering…" : hash ? "Regenerate" : "Generate"}
        </button>
        {hash && (
          <button
            style={{ marginTop: 10 }}
            className="danger"
            disabled={busy}
            onClick={remove}
          >
            Delete
          </button>
        )}
      </div>
      <div className="hint">
        Generate an image to represent this character. Will make use of
        appearance descriptions and any trained LoRAs (if available)
      </div>
    </>
  );
}

const SINGLE_FIELDS: [keyof Character["appearance"], string][] = [
  ["hair_color", "Hair colour"],
  ["hair_style", "Hair style"],
  ["eye_color", "Eye colour"],
  ["eye_quality", "Eye quality"],
  ["face", "Face"],
  ["skin_tone", "Skin tone"],
  ["build", "Build"],
  ["height", "Height"],
  ["bust", "Bust"],
  ["butt", "Butt"],
  ["rest_expression", "Expression at rest"],
  ["posture", "Posture / carriage"],
];

// Common outfit archetypes, prefilled with a specific booru tag block --
// color, length, material, silhouette on every article -- so the set stays
// consistent across a sheet/LoRA batch (see the hint above the list). Tags
// describe clothing only, never hair/eyes/body -- those are locked identity
// tags and belong on the Appearance tab, not here.
const OUTFIT_PRESETS: {
  id: string;
  name: string;
  tags: string;
  negative: string;
}[] = [
  {
    id: "schoolgirl",
    name: "Schoolgirl",
    tags:
      "school uniform, sailor collar, white sailor shirt, short sleeves, red neckerchief, " +
      "pleated skirt, navy pleated skirt, knee-length skirt, white knee-high socks, brown loafers",
    negative: "",
  },
  {
    id: "nurse",
    name: "Nurse",
    tags:
      "nurse, white nurse cap, red cross print, white nurse dress, short sleeves, " +
      "knee-length dress, white pantyhose, white nurse shoes",
    negative: "",
  },
  {
    id: "maid",
    name: "Maid",
    tags:
      "maid, white frilled headdress, black maid dress, long sleeves, knee-length dress, " +
      "white frilled apron, white thighhighs, black mary janes",
    negative: "",
  },
  {
    id: "goth",
    name: "Goth",
    tags:
      "gothic fashion, black gothic dress, long sleeves, lace trim, floor-length skirt, " +
      "black choker, black fingerless gloves, dark red lipstick, black platform boots",
    negative: "",
  },
  {
    id: "professor",
    name: "Professor",
    tags:
      "black blazer, white collared shirt, black pencil skirt, knee-length skirt, " +
      "rectangular glasses, black sheer pantyhose, black high heels",
    negative: "",
  },
  {
    id: "housewife",
    name: "Housewife",
    tags:
      "light pink apron, white lace trim apron, floral print dress, knee-length dress, " +
      "short sleeves, low heels",
    negative: "",
  },
  {
    id: "office",
    name: "Office",
    tags:
      "charcoal business suit, black blazer, white collared shirt, black pencil skirt, " +
      "knee-length skirt, sheer black pantyhose, black high heels",
    negative: "",
  },
  {
    id: "swimsuit",
    name: "Swimsuit",
    tags: "blue bikini, string bikini, side-tie bikini bottom, bare shoulders, sandals",
    negative: "one-piece swimsuit",
  },
  {
    id: "gym",
    name: "Gym",
    tags: "gym uniform, white sports bra, black bike shorts, white ankle socks, white sneakers",
    negative: "",
  },
  {
    id: "casual",
    name: "Casual",
    tags: "white t-shirt, short sleeves, blue denim shorts, crew socks, white sneakers",
    negative: "",
  },
  {
    id: "idol",
    name: "Idol",
    tags:
      "idol costume, sparkly sleeveless top, pleated mini skirt, white knee-high boots, " +
      "fingerless gloves, hair ornament",
    negative: "",
  },
  {
    id: "bunny",
    name: "Bunny Girl",
    tags:
      "playboy bunny, black bunny suit, detached collar, wrist cuffs, fluffy bunny tail, " +
      "black pantyhose, black high heels",
    negative: "",
  },
  {
    id: "miko",
    name: "Shrine Maiden",
    tags:
      "miko, white haori, wide sleeves, red hakama, ankle-length hakama, white tabi, " +
      "zouri sandals",
    negative: "",
  },
  {
    id: "yukata",
    name: "Yukata",
    tags:
      "yukata, floral print yukata, wide sleeves, ankle-length, wide obi sash, " +
      "wooden geta sandals",
    negative: "",
  },
  {
    id: "witch",
    name: "Witch",
    tags:
      "wide-brim witch hat, black witch robe, long sleeves, floor-length robe, " +
      "tattered cape, thigh-high boots",
    negative: "",
  },
  {
    id: "waitress",
    name: "Waitress",
    tags:
      "cafe uniform, black short-sleeve dress, knee-length dress, white frilled apron, " +
      "white headband, black mary janes",
    negative: "",
  },
  {
    id: "cheerleader",
    name: "Cheerleader",
    tags:
      "cheerleader uniform, sleeveless crop top, pleated mini skirt, white knee-high socks, " +
      "white sneakers, pom-poms",
    negative: "",
  },
  {
    id: "pajamas",
    name: "Pajamas",
    tags: "pajamas, loose short-sleeve top, pajama shorts, fuzzy slippers",
    negative: "",
  },
];

function AppearanceTab({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  const store = useStore();
  const vocab = store.vocab?.tags ?? {};

  const setField = (key: string, value: TaggedField) =>
    save({ ...draft, appearance: { ...draft.appearance, [key]: value } });

  return (
    <>
      <CollapsibleCard title="Appearance">
        {SINGLE_FIELDS.map(([key, label]) => (
          <TagField
            key={`${draft.id}:${key as string}`}
            label={label}
            options={vocab[key as string] ?? []}
            value={draft.appearance[key] as TaggedField}
            onChange={(v) => setField(key as string, v)}
          />
        ))}
        <div className="hint">
          Expression at rest and posture describe the character for the writer
          model only — neither is baked into image prompts. Every generator
          already fills a pose and an expression of its own (the director's
          shot, the sheet's angle, the sprite slot), and a stance locked in
          here fought all of them.
        </div>

        <MultiTagField
          label="Distinguishing marks"
          options={vocab.distinguishing ?? []}
          values={draft.appearance.distinguishing}
          onChange={(v) =>
            save({
              ...draft,
              appearance: { ...draft.appearance, distinguishing: v },
            })
          }
        />
        <MultiTagField
          label="Accessories"
          options={vocab.accessories ?? []}
          values={draft.appearance.accessories}
          onChange={(v) =>
            save({
              ...draft,
              appearance: { ...draft.appearance, accessories: v },
            })
          }
        />
        <div className="hint">
          Every mark and accessory added here can be switched off per outfit on
          the Outfits card below, so a chest tattoo doesn't get drawn on top of
          a shirt. Genital descriptors live here too now — add them as marks.
        </div>

        <MultiTagField
          label="Negatives"
          negative
          options={vocab.negatives ?? []}
          values={draft.appearance.negatives}
          onChange={(v) =>
            save({
              ...draft,
              appearance: { ...draft.appearance, negatives: v },
            })
          }
        />
      </CollapsibleCard>

    </>
  );
}


function OutfitsTab({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  return (
    <>
      <CollapsibleCard title="Outfits">
        <div className="hint">
          Add as many as you think you will need of BEFORE lora training. Be
          specific with every article style/color/length/etc within a given
          outfit for consistency.
        </div>
        {draft.outfits.map((outfit, index) => (
          <div
            key={outfit.id}
            className="row"
            style={{ marginBottom: 6, alignItems: "flex-start" }}
          >
            <div style={{ flex: 1 }}>
              <div className="grid2">
                <Text
                  label="Name"
                  value={outfit.name}
                  onChange={(v) =>
                    save({
                      ...draft,
                      outfits: draft.outfits.map((o, i) =>
                        i === index ? { ...o, name: v } : o,
                      ),
                    })
                  }
                />
                <div className="field">
                  <label>Default</label>
                  <input
                    type="radio"
                    checked={draft.default_outfit === outfit.id}
                    onChange={() =>
                      save({ ...draft, default_outfit: outfit.id })
                    }
                  />
                </div>
              </div>
              <TagInput
                label="Tags"
                value={outfit.tags}
                prefer={["clothing", "accessory", "body", "detail"]}
                onChange={(v) =>
                  save({
                    ...draft,
                    outfits: draft.outfits.map((o, i) =>
                      i === index ? { ...o, tags: v } : o,
                    ),
                  })
                }
              />
              {draft.appearance.distinguishing.length > 0 && (
                <OutfitVisibilityRow
                  label="Visible marks (tap to hide for this outfit)"
                  items={draft.appearance.distinguishing}
                  hidden={outfit.hidden_marks ?? []}
                  onToggle={(hidden_marks) =>
                    save({
                      ...draft,
                      outfits: draft.outfits.map((o, i) =>
                        i === index ? { ...o, hidden_marks } : o,
                      ),
                    })
                  }
                />
              )}
              {draft.appearance.accessories.length > 0 && (
                <OutfitVisibilityRow
                  label="Visible accessories (tap to hide for this outfit)"
                  items={draft.appearance.accessories}
                  hidden={outfit.hidden_accessories ?? []}
                  onToggle={(hidden_accessories) =>
                    save({
                      ...draft,
                      outfits: draft.outfits.map((o, i) =>
                        i === index ? { ...o, hidden_accessories } : o,
                      ),
                    })
                  }
                />
              )}
            </div>
            <button
              className="tiny danger"
              onClick={() =>
                save({
                  ...draft,
                  outfits: draft.outfits.filter((_, i) => i !== index),
                })
              }
            >
              ×
            </button>
          </div>
        ))}
        <div className="row" style={{ gap: 8 }}>
          <button
            onClick={() =>
              save({
                ...draft,
                outfits: [
                  ...draft.outfits,
                  {
                    id: `outfit${draft.outfits.length + 1}`,
                    name: `Outfit ${draft.outfits.length + 1}`,
                    tags: "",
                    negative: "",
                    hidden_marks: [],
                    hidden_accessories: [],
                  },
                ],
              })
            }
          >
            + Add outfit
          </button>
          <button
            disabled={draft.outfits.some((o) => o.id === "none")}
            onClick={() =>
              save({
                ...draft,
                outfits: [
                  ...draft.outfits,
                  {
                    id: "none",
                    name: "None",
                    tags: "nude",
                    negative: "clothes, clothing",
                    hidden_marks: [],
                    hidden_accessories: [],
                  },
                ],
              })
            }
          >
            + Add nude outfit
          </button>
        </div>
        <div className="hint" style={{ marginTop: 8, marginBottom: 4 }}>
          Quick add a common archetype (hover to preview tags):
        </div>
        <div className="chip-grid">
          {OUTFIT_PRESETS.map((preset) => {
            const used = draft.outfits.some((o) => o.id === preset.id);
            return (
              <span
                key={preset.id}
                className="chip"
                style={used ? { opacity: 0.4, cursor: "default" } : undefined}
                title={used ? "Already added" : preset.tags}
                onClick={() => {
                  if (used) return;
                  save({
                    ...draft,
                    outfits: [...draft.outfits, { ...preset }],
                  });
                }}
              >
                {preset.name}
              </span>
            );
          })}
        </div>
      </CollapsibleCard>

      <PromptPreviewCard character={draft} />
    </>
  );
}

function TagField({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { label: string; tag: string }[];
  value: TaggedField;
  onChange: (v: TaggedField) => void;
}) {
  const known = options.find((o) => o.label === value?.label);
  const filled = !!(value?.tag?.trim() || value?.label?.trim());
  // "custom…" has to be sticky. The select's value is otherwise derived from
  // the field, so picking it on an empty field produced nothing to derive
  // from and the dropdown snapped straight back to "—" -- which is why it
  // looked unselectable. Sticky until a real option (or "—") is picked.
  const [pinned, setPinned] = useState(false);
  const custom = !known && (pinned || filled);

  return (
    <div className="grid2" style={{ alignItems: "end" }}>
      <div className="field">
        <label>{label}</label>
        <select
          value={known ? value.label : custom ? "__free" : ""}
          onChange={(e) => {
            const picked = e.target.value;
            setPinned(picked === "__free");
            if (picked === "__free") {
              // Keep whatever tag is already typed as the starting point, but
              // drop the preset label so the prompt builders read the tag box.
              const tag = value?.tag ?? "";
              onChange({ label: tag, tag });
              return;
            }
            const hit = options.find((o) => o.label === picked);
            onChange(hit ? { label: hit.label, tag: hit.tag } : { label: "", tag: "" });
          }}
        >
          <option value="">—</option>
          {options.map((o) => (
            <option key={o.label} value={o.label}>
              {o.label}
            </option>
          ))}
          <option value="__free">custom…</option>
        </select>
      </div>
      <TagInput
        label="booru tag"
        value={value?.tag ?? ""}
        placeholder="free text falls back to a raw tag"
        onChange={(tag) => {
          // On a custom field the label mirrors the tag (as MultiTagField
          // already does), so the writer model gets the typed words instead
          // of a stale preset name or a blank.
          onChange(
            custom || !known
              ? { label: tag, tag }
              : { label: value?.label || tag, tag },
          );
        }}
      />
    </div>
  );
}

function MultiTagField({
  label,
  options,
  values,
  onChange,
  negative = false,
}: {
  label: string;
  options: { label: string; tag: string }[];
  values: TaggedField[];
  onChange: (v: TaggedField[]) => void;
  negative?: boolean;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <div className="chip-grid" style={{ marginBottom: 6 }}>
        {options.map((option) => {
          const on = values.some((v) => v.label === option.label);
          return (
            <span
              key={option.label}
              className={`chip ${negative ? "negative" : ""} ${on ? "on" : ""}`}
              onClick={() =>
                onChange(
                  on
                    ? values.filter((v) => v.label !== option.label)
                    : [...values, { label: option.label, tag: option.tag }],
                )
              }
            >
              {option.label}
            </span>
          );
        })}
      </div>
      {values
        .map((v, i) => ({ v, i }))
        .filter(({ v }) => !options.some((o) => o.label === v.label))
        .map(({ v, i }) => (
          <div key={i} className="row" style={{ marginBottom: 3 }}>
            <input
              style={{ flex: 1 }}
              value={v.tag}
              placeholder="custom tag"
              onChange={(e) =>
                // Label mirrors the typed tag -- it's what shows on the
                // per-outfit visibility chips and what the writer model
                // sees, so it should never sit frozen at "custom N".
                onChange(
                  values.map((x, xi) =>
                    xi === i
                      ? { ...x, tag: e.target.value, label: e.target.value }
                      : x,
                  ),
                )
              }
            />
            <button
              className="tiny danger"
              onClick={() => onChange(values.filter((_, xi) => xi !== i))}
            >
              ×
            </button>
          </div>
        ))}
      <button
        className="tiny"
        onClick={() => onChange([...values, { label: "", tag: "" }])}
      >
        + custom
      </button>
    </div>
  );
}

// Per-outfit visibility toggle for the character's own distinguishing marks
// or accessories -- a chip lit up means it renders with this outfit; tapping
// it off adds the mark's label to the outfit's hidden list (Outfit.hidden_marks
// / .hidden_accessories), matched by label since TaggedField has no id.
function OutfitVisibilityRow({
  label,
  items,
  hidden,
  onToggle,
}: {
  label: string;
  items: TaggedField[];
  hidden: string[];
  onToggle: (hidden: string[]) => void;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <div className="chip-grid">
        {items
          .filter((item) => item.label.trim())
          .map((item, i) => {
            const visible = !hidden.includes(item.label);
            return (
              <span
                key={i}
                className={`chip ${visible ? "on" : ""}`}
                title={item.tag}
                onClick={() =>
                  onToggle(
                    visible
                      ? [...hidden, item.label]
                      : hidden.filter((l) => l !== item.label),
                  )
                }
              >
                {item.label}
              </span>
            );
          })}
      </div>
    </div>
  );
}

/**
 * Where this character starts on each of the project's stats.
 *
 * Only overrides live here — a blank field means the stat's own default, so a
 * cast of six characters needs entries only where someone differs (the
 * childhood friend who begins at affection 30). The schema itself is
 * project-wide and lives on the Setting tab.
 */
function StatsCard({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  const store = useStore();
  const stats = store.project?.character_stats ?? [];
  if (!stats.length || draft.is_player) return null;

  const overrides = draft.stat_overrides ?? {};
  const set = (id: string, value: string) => {
    const next = { ...overrides };
    if (value === "") delete next[id];
    else next[id] = value;
    save({ ...draft, stat_overrides: next });
  };

  return (
    <CollapsibleCard title="Stats (starting values)">
      <div className="hint" style={{ marginBottom: 8 }}>
        Blank uses the stat's own default. These persist for the whole
        playthrough and compile to <code>{"{stat}_" + draft.id}</code>, which
        choice effects can nudge and conditions can gate on.
      </div>
      {stats.map((stat) => (
        <Text
          key={stat.id}
          label={`${stat.name || stat.id} (default ${stat.default || "0"})`}
          value={overrides[stat.id] ?? ""}
          onChange={(v) => set(stat.id, v)}
        />
      ))}
    </CollapsibleCard>
  );
}

function PersonalityTab({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  const p = draft.personality;
  const set = (patch: Partial<typeof p>) =>
    save({ ...draft, personality: { ...p, ...patch } });
  return (
    <>
      <StatsCard draft={draft} save={save} />
      <CollapsibleCard title="Personality (writer model only)">
        <Area
          label="Summary"
          rows={3}
          value={p.summary}
          onChange={(v) => set({ summary: v })}
        />

        <Area
          label="Background / history"
          rows={3}
          value={p.background}
          onChange={(v) => set({ background: v })}
        />

        <Area
          label="Relationship to the player"
          rows={2}
          value={p.relationship_to_player}
          onChange={(v) => set({ relationship_to_player: v })}
        />

        <Area
          label="Speech style"
          rows={2}
          value={p.speech_style}
          onChange={(v) => set({ speech_style: v })}
        />
        <div className="hint" style={{ marginTop: -4, marginBottom: 8 }}>
          <em>How</em> they build sentences — length, rhythm, verbal tics,
          vocabulary. This is what keeps their dialogue from flattening toward a
          generic voice.
        </div>

        <Select
          label="Speech register"
          value={p.speech_register}
          options={[
            ["", "—"],
            ...(useStore.getState().vocab?.tags.speech_register ?? []).map(
              (o) => [o.label, o.label] as [string, string],
            ),
          ]}
          onChange={(v) => set({ speech_register: v })}
        />
        <div className="hint" style={{ marginTop: -4, marginBottom: 8 }}>
          Formality level the writer model should match — polite/keigo vs. blunt
          vs. slangy. Paired with speech style in the character card.
        </div>
      </CollapsibleCard>

      <CollapsibleCard title="Inner life">
        <Area
          label="Wants"
          rows={2}
          value={p.wants}
          onChange={(v) => set({ wants: v })}
        />
        <Area
          label="Fears"
          rows={2}
          value={p.fears}
          onChange={(v) => set({ fears: v })}
        />

        <Text
          label="Loves"
          value={p.loves}
          placeholder="the sea in winter, being trusted"
          onChange={(v) => set({ loves: v })}
        />
        <Text
          label="Likes"
          value={p.likes}
          onChange={(v) => set({ likes: v })}
        />
        <Text
          label="Dislikes"
          value={p.dislikes}
          onChange={(v) => set({ dislikes: v })}
        />
        <Text
          label="Hobbies"
          value={p.hobbies}
          onChange={(v) => set({ hobbies: v })}
        />
        <div className="hint">
          None of this reaches an image prompt. Loves and dislikes steer what a
          character brings up or avoids; history grounds their reactions.
        </div>
      </CollapsibleCard>

      <CollapsibleCard title="NSFW (adult scenes only)">
        <Area
          label="In the adult register"
          rows={4}
          value={p.nsfw ?? ""}
          placeholder="what they want and will not ask for, what they do when they stop performing, where the guard drops and where it does not"
          onChange={(v) => set({ nsfw: v })}
        />
        <div className="hint">
          Only reaches the writer for a scene with <b>NSFW</b> ticked, and is
          absent from every other prompt rather than present under an
          instruction to ignore it. It adds to the personality above; it does
          not replace it — the same voice, doing this.
        </div>
      </CollapsibleCard>
    </>
  );
}

function VoiceTab({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  const store = useStore();
  // Takes live on the character record, not local state, so they survive
  // leaving the tab. Only the in-flight flag has to be local.
  const candidates = draft.voice.audition ?? [];
  const [testing, setTesting] = useState(false);
  const clipHash =
    (draft.voice.reference_clip || "").split("/").pop()?.split(".")[0] || "";
  const [emotion, setEmotion] = useState("melancholy");
  const [intensity, setIntensity] = useState(0.7);
  const [resolved, setResolved] = useState<any>(null);

  useEffect(() => {
    api
      .emotionPreview(store.project!.id, draft.id, emotion, intensity)
      .then(setResolved)
      .catch(() => setResolved(null));
  }, [draft.id, emotion, intensity, draft.voice.emotion_scale]);

  const set = (patch: Partial<Character["voice"]>) =>
    save({ ...draft, voice: { ...draft.voice, ...patch } });

  return (
    <>
      <CollapsibleCard title="Casting">
        <div className="row" style={{ marginBottom: 8 }}>
          <label className="tiny" style={{ flex: 1 }}>
            <span className="hint">
              {draft.voice.reference_clip
                ? "Replace the reference clip"
                : "Upload a ~8-15s reference clip"}
            </span>
            <input
              type="file"
              accept="audio/*"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                try {
                  const out = await api.uploadVoiceClip(
                    store.project!.id,
                    draft.id,
                    file,
                  );
                  store.notify(out.lines_stale
                    ? `reference clip frozen — ${out.lines_stale} line${
                        out.lines_stale === 1 ? "" : "s"
                      } already voiced in the old clip are now stale`
                    : "reference clip frozen");
                  await store.refreshCharacters();
                } catch (err) {
                  store.fail(String(err));
                }
              }}
            />
          </label>
          <button
            disabled={!draft.voice.reference_clip || testing}
            title={
              draft.voice.reference_clip
                ? "One take per emotion, read with a line that fits it"
                : "Upload a reference clip first — IndexTTS-2 auditions a clip, not a description"
            }
            onClick={async () => {
              try {
                setTesting(true);
                const { job_id } = await api.castVoice(store.project!.id, {
                  character_id: draft.id,
                  takes: 0,
                });
                store.notify(
                  "auditioning — takes appear when the job finishes",
                );
                store.refreshJobs();
                const poll = setInterval(async () => {
                  const job = await api.getJob(job_id);
                  if (job.status === "done") {
                    clearInterval(poll);
                    setTesting(false);
                    await store.refreshCharacters();
                  } else if (["error", "cancelled"].includes(job.status)) {
                    clearInterval(poll);
                    setTesting(false);
                    if (job.error) store.fail(job.error.split("\n")[0]);
                  }
                }, 1500);
              } catch (e) {
                setTesting(false);
                store.fail(String(e));
              }
            }}
          >
            {testing ? "Testing…" : "Test emotion range"}
          </button>
        </div>

        <div className="hint" style={{ marginTop: -4, marginBottom: 10 }}>
          <strong>
            Don't clone a real person's voice without their explicit consent
          </strong>{" "}
          Use a description-based voice generation model like{" "}
          <a
            href="https://github.com/resemble-ai/chatterbox"
            target="_blank"
            rel="noreferrer"
          >
            Chatterbox
          </a>
          , if you want to generate a seed clip without any human voice acting.
        </div>

        {clipHash ? (
          <div className="field">
            <label>Current timbre reference (frozen)</label>
            <audio
              controls
              style={{ width: "100%" }}
              src={api.assetUrl(clipHash)}
            />
            <div className="hint tiny">{draft.voice.reference_clip}</div>
          </div>
        ) : (
          <div className="hint" style={{ marginBottom: 8 }}>
            No clip frozen yet — this character cannot be voiced until one is
            uploaded.
          </div>
        )}
      </CollapsibleCard>

      {candidates.length > 0 && (
        <CollapsibleCard title="Audition takes">
          <div className="hint" style={{ marginBottom: 8 }}>
            Previews of the voice you already froze — not candidates to swap in.
            Every take clones the same clip, so the timbre cannot change; what
            you are listening for is how much of the voice <em>survives</em>{" "}
            each emotion. If the angry or afraid take stops sounding like the
            character, lower Emotion scale below and test again.
          </div>
          {candidates.map((candidate) => (
            <div key={candidate.hash} style={{ marginBottom: 10 }}>
              <div className="tiny" style={{ marginBottom: 2 }}>
                <strong>{candidate.emotion}</strong>
                <span className="hint"> · {candidate.line}</span>
              </div>
              <audio
                controls
                style={{ width: "100%" }}
                src={api.assetUrl(candidate.preview_hash || candidate.hash)}
              />
            </div>
          ))}
        </CollapsibleCard>
      )}

      <CollapsibleCard title="Delivery">
        <Check
          label="Voiced"
          checked={draft.voice.voiced}
          onChange={(v) => set({ voiced: v })}
        />
        <Range
          label="Emotion scale (caps timbre leakage on strong emotion)"
          value={draft.voice.emotion_scale}
          max={1.4}
          onChange={(v) => set({ emotion_scale: v })}
        />
        <Range
          label="Speed"
          value={draft.voice.speed}
          min={0.7}
          max={1.3}
          onChange={(v) => set({ speed: v })}
        />
      </CollapsibleCard>

      <CollapsibleCard title="Emotion preview">
        <div className="grid2">
          <Select
            label="Emotion"
            value={emotion}
            options={(store.vocab?.emotions ?? []).map(
              (e) => [e, e] as [string, string],
            )}
            onChange={setEmotion}
          />
          <Range label="Intensity" value={intensity} onChange={setIntensity} />
        </div>
        {resolved && (
          <table className="data">
            <tbody>
              {Object.entries(resolved.vec)
                .filter(([, v]) => Number(v) > 0)
                .map(([k, v]) => (
                  <tr key={k}>
                    <th>{k}</th>
                    <td>{Number(v).toFixed(3)}</td>
                  </tr>
                ))}
              <tr>
                <th>sum</th>
                <td>
                  {Object.values(resolved.vec)
                    .reduce((a: number, b: any) => a + Number(b), 0)
                    .toFixed(3)}{" "}
                  / 1.5
                </td>
              </tr>
              <tr>
                <th>speed</th>
                <td>{resolved.speed}</td>
              </tr>
              <tr>
                <th>emo_alpha</th>
                <td>{resolved.emo_alpha}</td>
              </tr>
              {resolved.gain_db !== 0 && (
                <tr>
                  <th>gain</th>
                  <td>{resolved.gain_db} dB</td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </CollapsibleCard>
    </>
  );
}

const ZOOM = 2.5;
const clamp = (v: number, m: number) => Math.max(-m, Math.min(m, v));

// The lightbox image: tap toggles a 2.5x zoom centred on the point tapped,
// drag pans while zoomed (clamped to the image edges). When not zoomed and
// swipe handlers are passed (the cull lightbox), a horizontal drag is a
// keep/delete swipe instead -- `onSwipeProgress` feeds the parent's verdict
// overlays. Zoom resets whenever `src` changes (stepping slots) so a new
// image always opens fit-to-frame.
function ZoomableImg({
  src,
  className,
  onSwipeProgress,
  onSwipeLeft,
  onSwipeRight,
}: {
  src: string;
  className?: string;
  onSwipeProgress?: (dx: number) => void;
  onSwipeLeft?: () => void;
  onSwipeRight?: () => void;
}) {
  const [zoom, setZoom] = useState(false);
  const [off, setOff] = useState({ x: 0, y: 0 });
  const g = useRef<{
    x: number;
    y: number;
    ox: number;
    oy: number;
    moved: boolean;
  } | null>(null);
  const swipeable = !zoom && !!(onSwipeLeft || onSwipeRight);

  // Half the slack a scaled image has inside its own box -- the pan limit.
  const panMax = (el: HTMLImageElement) => ({
    x: (el.offsetWidth * (ZOOM - 1)) / 2,
    y: (el.offsetHeight * (ZOOM - 1)) / 2,
  });

  useEffect(() => {
    setZoom(false);
    setOff({ x: 0, y: 0 });
    g.current = null;
  }, [src]);

  return (
    <img
      src={src}
      className={className}
      draggable={false}
      style={{
        transform: zoom
          ? `translate(${off.x}px, ${off.y}px) scale(${ZOOM})`
          : `translateX(${off.x}px) rotate(${off.x / 40}deg)`,
        transition: g.current ? "none" : "transform 0.15s",
        cursor: zoom ? "zoom-out" : "zoom-in",
        touchAction: "none",
      }}
      onClick={(e) => e.stopPropagation()}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture?.(e.pointerId);
        g.current = {
          x: e.clientX,
          y: e.clientY,
          ox: off.x,
          oy: off.y,
          moved: false,
        };
      }}
      onPointerMove={(e) => {
        if (!g.current) return;
        const dx = e.clientX - g.current.x;
        const dy = e.clientY - g.current.y;
        if (Math.abs(dx) > 4 || Math.abs(dy) > 4) g.current.moved = true;
        if (zoom) {
          const m = panMax(e.currentTarget);
          setOff({
            x: clamp(g.current.ox + dx, m.x),
            y: clamp(g.current.oy + dy, m.y),
          });
        } else if (swipeable) {
          setOff({ x: dx, y: 0 });
          onSwipeProgress?.(dx);
        }
      }}
      onPointerUp={(e) => {
        const st = g.current;
        g.current = null;
        if (!st) return;
        const dx = e.clientX - st.x;
        if (!st.moved) {
          if (zoom) {
            setZoom(false);
            setOff({ x: 0, y: 0 });
          } else {
            // Offset the clicked point to where the centre would zoom it, so
            // it stays under the cursor; then clamp to the image edges.
            const el = e.currentTarget;
            const r = el.getBoundingClientRect();
            const m = panMax(el);
            setOff({
              x: clamp((e.clientX - (r.left + r.width / 2)) * (1 - ZOOM), m.x),
              y: clamp((e.clientY - (r.top + r.height / 2)) * (1 - ZOOM), m.y),
            });
            setZoom(true);
          }
          onSwipeProgress?.(0);
          return;
        }
        if (zoom) return;
        if (swipeable) {
          if (dx > 90) onSwipeRight?.();
          else if (dx < -90) onSwipeLeft?.();
          setOff({ x: 0, y: 0 });
          onSwipeProgress?.(0);
        }
      }}
      onPointerCancel={() => {
        g.current = null;
        if (!zoom) {
          setOff({ x: 0, y: 0 });
          onSwipeProgress?.(0);
        }
      }}
    />
  );
}

// Mirrors backend images.DEFAULT_SHEET_ANGLES -- there's no larger
// vocabulary to draw from, this is the whole curated set.
const SHEET_ANGLES = [
  "facing viewer, looking at viewer",
  "from side, profile",
  "from behind, looking back",
  "three-quarter view",
  "from above",
  "from below",
];

/**
 * The Sprite LoRA's own bootstrap loop -- shoot the character sheet, cull
 * it, caption it, train. Split out of the Sprites tab so that tab is just
 * the compiled-sprite generator; this is the LoRA lifecycle, same shape as
 * the CG LoRA tab below (generate → cull → caption/train), just pointed at
 * sheet_pool and lora instead of cg_pool and lora_cg.
 */
function SpriteLoraTab({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  const store = useStore();
  const pool = draft.assets.sheet_pool;
  const uncaptioned = pool.filter((p) => !p.caption);
  const [excludedOutfits, setExcludedOutfits] = useState<Set<string>>(
    new Set(),
  );
  const [sheetAngles, setSheetAngles] = useState<string[]>(SHEET_ANGLES);
  const [sheetExpressions, setSheetExpressions] = useState<string[]>([
    "neutral",
    "smiling",
    "angry",
    "crying",
    "surprised",
  ]);
  const [sheetCount, setSheetCount] = useState(30);
  const [generating, setGenerating] = useState(false);
  const [captioning, setCaptioning] = useState(false);
  const [poolRegen, setPoolRegen] = useState<Set<string>>(new Set());
  const [confirmClearPool, setConfirmClearPool] = useState(false);

  const selectedOutfits = draft.outfits.filter(
    (o) => !excludedOutfits.has(o.id),
  );
  const noOutfits = draft.outfits.length === 0;
  const outfitIds = noOutfits ? [] : selectedOutfits.map((o) => o.id);
  const canGenerate =
    sheetAngles.length > 0 &&
    sheetExpressions.length > 0 &&
    sheetCount > 0 &&
    (noOutfits || selectedOutfits.length > 0);
  // The size of the space being sampled from, not the size of the run --
  // shown so it's obvious when the count is far below full coverage.
  const comboCount =
    (noOutfits ? 1 : selectedOutfits.length) *
    sheetAngles.length *
    sheetExpressions.length;

  // After a save that drops a hash from this character's record, best-effort
  // purge the file from disk and the Gallery too -- otherwise "delete" only
  // unlinks it here and the image lingers as an orphan everywhere else. Must
  // run AFTER the save resolves: the backend refuses to delete anything the
  // character record still references (assets.py _referenced), so purging
  // too early just 404/409s. Never force -- that would mean something else
  // still points at this hash, which is worth leaving alone.
  const purgeAsset = (hash: string) => {
    api.deleteGalleryAsset(store.project!.id, hash).catch(() => {});
  };

  const deleteItem = async (hash: string) => {
    await save({
      ...draft,
      assets: {
        ...draft.assets,
        sheet_pool: pool.filter((p) => p.hash !== hash),
      },
    });
    purgeAsset(hash);
  };

  // Regenerate one pool image: queue a fresh sheet render for the same
  // outfit/angle/expression (new seed) and drop the old one now. image.sheet
  // always appends, so the replacement lands at the end of the pool when the
  // job finishes -- nothing writes back in place the way image.sprite does.
  const regeneratePoolItem = async (item: SheetPoolItem) => {
    setPoolRegen((prev) => new Set(prev).add(item.hash));
    const clear = () =>
      setPoolRegen((prev) => {
        const next = new Set(prev);
        next.delete(item.hash);
        return next;
      });
    try {
      const { job_id } = await api.generateSheet(store.project!.id, {
        character_id: draft.id,
        // One image, not the endpoint's default 30: the selection below is a
        // single outfit/angle/expression cell, so a count of 30 re-rendered
        // that one cell 30 times over.
        count: 1,
        outfit_ids: [item.outfit_id],
        angles: [item.angle],
        expressions: [item.expression],
      });
      // Enqueued OK -- retire the old image (the job re-fetches the character
      // well after this save, so it won't clobber the removal) and purge it
      // once the removal has actually landed.
      await save({
        ...draft,
        assets: {
          ...draft.assets,
          sheet_pool: pool.filter((p) => p.hash !== item.hash),
        },
      });
      purgeAsset(item.hash);
      store.notify("regenerating pool image");
      store.refreshJobs();
      const poll = setInterval(async () => {
        const job = await api.getJob(job_id);
        await store.refreshCharacters();
        if (job.status === "done") {
          clearInterval(poll);
          clear();
          store.notify("pool image regenerated");
        } else if (["error", "cancelled"].includes(job.status)) {
          clearInterval(poll);
          clear();
          if (job.error) store.fail(job.error.split("\n")[0]);
        }
      }, 2000);
    } catch (e) {
      clear();
      store.fail(String(e));
    }
  };

  // Full-screen cull: page through the pool one image at a time, keeping or
  // deleting each, until the list runs out (or the user bails with Esc/close).
  const [cullIndex, setCullIndex] = useState<number | null>(null);
  const [swipe, setSwipe] = useState(0); // live swipe offset in px (ZoomableImg)
  const cullItem = cullIndex !== null ? pool[cullIndex] : null;

  const closeCull = () => {
    setSwipe(0);
    setCullIndex(null);
  };

  const cullKeep = () => {
    setSwipe(0);
    setCullIndex((i) => (i === null || i + 1 >= pool.length ? null : i + 1));
  };

  const cullDelete = () => {
    if (cullIndex === null) return;
    const nextPool = pool.filter((p) => p.hash !== pool[cullIndex].hash);
    deleteItem(pool[cullIndex].hash); // saves + purges the file from disk/Gallery
    setSwipe(0);
    // The list shrank under us: the same index now points at the next image.
    setCullIndex((i) => (i === null || i >= nextPool.length ? null : i));
  };

  const cullRegenerate = () => {
    if (cullIndex === null) return;
    const nextLen = pool.length - 1; // regeneratePoolItem retires this one
    regeneratePoolItem(pool[cullIndex]);
    setSwipe(0);
    setCullIndex((i) => (i === null || i >= nextLen ? null : i));
  };

  useEffect(() => {
    if (cullIndex === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeCull();
      else if (e.key === "ArrowRight") cullKeep();
      else if (e.key === "ArrowLeft") cullDelete();
      else if (e.key.toLowerCase() === "r") cullRegenerate();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cullIndex, pool]);

  return (
    <>
      <CollapsibleCard title="1 · Character sheet">
        <div className="hint" style={{ marginBottom: 8 }}>
          Studio shots — solo, full body, white background — because this LoRA's
          job is drawing sprites, and its dataset should match what it will be
          asked for. Action, other people in frame and real backdrops live on
          the CG LoRA tab instead. Generates new images and adds them to the
          pool below; it never replaces what's already there.
        </div>
        {!noOutfits && (
          <div className="field">
            <label>Outfits</label>
            <div className="chip-grid">
              {draft.outfits.map((o) => {
                const on = !excludedOutfits.has(o.id);
                return (
                  <span
                    key={o.id}
                    className={`chip ${on ? "on" : ""}`}
                    onClick={() => {
                      const next = new Set(excludedOutfits);
                      if (on) next.add(o.id);
                      else next.delete(o.id);
                      setExcludedOutfits(next);
                    }}
                  >
                    {o.name}
                  </span>
                );
              })}
            </div>
          </div>
        )}
        <div className="field">
          <label>Angles</label>
          <div className="chip-grid">
            {SHEET_ANGLES.map((angle) => (
              <span
                key={angle}
                className={`chip ${sheetAngles.includes(angle) ? "on" : ""}`}
                onClick={() =>
                  setSheetAngles(
                    sheetAngles.includes(angle)
                      ? sheetAngles.filter((a) => a !== angle)
                      : [...sheetAngles, angle],
                  )
                }
              >
                {angle.split(",")[0]}
              </span>
            ))}
          </div>
        </div>
        <div className="field">
          <label>Expressions</label>
          <div className="chip-grid">
            {(store.vocab?.expressions ?? []).map((expression) => (
              <span
                key={expression}
                className={`chip ${sheetExpressions.includes(expression) ? "on" : ""}`}
                onClick={() =>
                  setSheetExpressions(
                    sheetExpressions.includes(expression)
                      ? sheetExpressions.filter((e) => e !== expression)
                      : [...sheetExpressions, expression],
                  )
                }
              >
                {expression}
              </span>
            ))}
          </div>
        </div>
        <div className="grid2">
          <Num
            label="Count"
            step={1}
            value={sheetCount}
            onChange={(v) => setSheetCount(Math.max(1, Math.round(v)))}
          />
        </div>
        <div className="hint">
          {comboCount} distinct outfit × angle × expression combination(s)
          selected; this run samples {sheetCount} of them, every combination
          used once before any repeats. 15–30 is the working range for a LoRA —
          the full product is rarely worth the VRAM hours.
        </div>
        <button
          className="primary"
          disabled={!canGenerate || generating}
          onClick={async () => {
            try {
              setGenerating(true);
              const { job_id } = await api.generateSheet(store.project!.id, {
                character_id: draft.id,
                count: sheetCount,
                outfit_ids: outfitIds,
                angles: sheetAngles,
                expressions: sheetExpressions,
              });
              store.notify(`sheet queued — ${sheetCount} image(s)`);
              store.refreshJobs();
              const poll = setInterval(async () => {
                const job = await api.getJob(job_id);
                await store.refreshCharacters(); // pool grows as images land
                if (job.status === "done") {
                  clearInterval(poll);
                  setGenerating(false);
                } else if (["error", "cancelled"].includes(job.status)) {
                  clearInterval(poll);
                  setGenerating(false);
                  if (job.error) store.fail(job.error.split("\n")[0]);
                }
              }, 3000);
            } catch (e) {
              setGenerating(false);
              store.fail(String(e));
            }
          }}
        >
          {generating ? "Generating…" : `Generate ${sheetCount} image(s)`}
        </button>
      </CollapsibleCard>

      {pool.length > 0 && (
        <CollapsibleCard
          title={`2 · Cull (${pool.length} in pool)`}
          headExtra={
            <button
              className="tiny danger"
              onClick={() => setConfirmClearPool(true)}
            >
              Clear all
            </button>
          }
        >
          <div className="hint" style={{ marginBottom: 8 }}>
            Delete the ones that don't agree with each other — whatever's left
            here is what training uses next, no picking required. Click an image
            to review the pool one at a time.
          </div>
          <div className="thumbs">
            {pool.map((item, i) => (
              <div key={item.hash} className="thumb">
                <img
                  src={api.assetUrl(item.hash)}
                  loading="lazy"
                  title="Open in review lightbox"
                  onClick={() => {
                    setSwipe(0);
                    setCullIndex(i);
                  }}
                />
                <button
                  className="regen"
                  title="Regenerate this image (new seed, same outfit/angle/expression)"
                  disabled={poolRegen.has(item.hash)}
                  onClick={() => regeneratePoolItem(item)}
                >
                  {poolRegen.has(item.hash) ? "…" : "⟳"}
                </button>
                <button
                  className="del"
                  title="Remove from the pool"
                  onClick={() => deleteItem(item.hash)}
                >
                  ×
                </button>
                <div className={`cap ${item.caption ? "captioned" : ""}`}>
                  {draft.outfits.find((o) => o.id === item.outfit_id)?.name ??
                    "default"}{" "}
                  · {item.expression} · {item.angle.split(",")[0]}
                </div>
              </div>
            ))}
          </div>
        </CollapsibleCard>
      )}

      {confirmClearPool && (
        <ConfirmDialog
          title={`Clear all ${pool.length} pool image(s)?`}
          confirmLabel="Clear pool"
          onCancel={() => setConfirmClearPool(false)}
          onConfirm={() => {
            setConfirmClearPool(false);
            save({ ...draft, assets: { ...draft.assets, sheet_pool: [] } });
          }}
        >
          <div className="hint">
            The images stay in the Gallery; only the pool reference goes away.
            Nothing has been captioned or trained on yet? This is safe. If
            you've already trained the Sprite LoRA on this pool, clearing it
            doesn't touch that LoRA — only what a future retrain would use.
          </div>
        </ConfirmDialog>
      )}

      {cullItem && cullIndex !== null && (
        <div className="lightbox-backdrop" onClick={closeCull}>
          <button
            className="lightbox-close"
            title="Close (Esc)"
            onClick={closeCull}
          >
            ×
          </button>
          <div className="lightbox-stage" onClick={(e) => e.stopPropagation()}>
            <span
              className="lightbox-verdict del"
              style={{ opacity: swipe < -20 ? Math.min(1, -swipe / 90) : 0 }}
            >
              Delete
            </span>
            <span
              className="lightbox-verdict keep"
              style={{ opacity: swipe > 20 ? Math.min(1, swipe / 90) : 0 }}
            >
              Keep
            </span>
            <ZoomableImg
              className="lightbox-img"
              src={api.assetUrl(cullItem.hash)}
              onSwipeProgress={setSwipe}
              onSwipeLeft={cullDelete}
              onSwipeRight={cullKeep}
            />
          </div>
          <div className="lightbox-meta" onClick={(e) => e.stopPropagation()}>
            {cullIndex + 1} / {pool.length} ·{" "}
            {draft.outfits.find((o) => o.id === cullItem.outfit_id)?.name ??
              "default"}{" "}
            · {cullItem.expression} · {cullItem.angle.split(",")[0]}
            <br />→ keep · ← delete · R regenerate · tap image to zoom
          </div>
          <div
            className="lightbox-actions"
            onClick={(e) => e.stopPropagation()}
          >
            <button className="danger" onClick={cullDelete}>
              Delete
            </button>
            <button onClick={cullRegenerate}>Regenerate</button>
            <button className="primary" onClick={cullKeep}>
              Keep
            </button>
          </div>
        </div>
      )}

      <CollapsibleCard title="3 · Caption and train">
        <div className="row" style={{ gap: 8 }}>
          <button
            disabled={uncaptioned.length === 0 || captioning}
            onClick={async () => {
              try {
                setCaptioning(true);
                const { job_id } = await api.captionImages(
                  store.project!.id,
                  draft.id,
                  uncaptioned.map((p) => p.hash),
                  "sheet",
                );
                store.notify(`captioning ${uncaptioned.length}`);
                store.refreshJobs();
                const poll = setInterval(async () => {
                  const job = await api.getJob(job_id);
                  await store.refreshCharacters();
                  if (job.status === "done") {
                    clearInterval(poll);
                    setCaptioning(false);
                    store.notify("captions ready");
                  } else if (["error", "cancelled"].includes(job.status)) {
                    clearInterval(poll);
                    setCaptioning(false);
                    if (job.error) store.fail(job.error.split("\n")[0]);
                  }
                }, 2000);
              } catch (e) {
                setCaptioning(false);
                store.fail(String(e));
              }
            }}
          >
            {captioning
              ? "Captioning…"
              : `Auto-caption (${uncaptioned.length} new)`}
          </button>
          <button
            className="primary"
            disabled={pool.length < 8}
            onClick={async () => {
              await api.trainLora(store.project!.id, {
                character_id: draft.id,
                slot: "sprite",
              });
              store.notify(
                "Sprite LoRA training queued — this takes 20-40 min on a 4090",
              );
              store.refreshJobs();
            }}
          >
            Train Sprite LoRA ({pool.length})
          </button>
        </div>
        <div className="hint">
          Trained on the character sheet above and loaded by every sprite. Once
          it exists, a <b>CG LoRA</b> tab appears next to Sprites — a second,
          optional LoRA trained on a varied dataset that event CGs prefer once
          it's trained, shot *with* this one.
        </div>
        {pool.length > 0 && pool.length < 8 && (
          <div className="hint warn">
            15–30 varied images is the working range. Quality matters far more
            than count.
          </div>
        )}
        {pool.length >= 8 && uncaptioned.length > 0 && (
          <div className="hint warn">
            {uncaptioned.length} image(s) not captioned yet — auto-caption first
            for better results.
          </div>
        )}
        <div className="grid2" style={{ marginTop: 8 }}>
          <Text
            label="Sprite LoRA path"
            value={draft.assets.lora ?? ""}
            onChange={(v) =>
              save({ ...draft, assets: { ...draft.assets, lora: v } })
            }
          />
          <Text
            label="Trigger word"
            value={draft.assets.trigger}
            onChange={(v) =>
              save({ ...draft, assets: { ...draft.assets, trigger: v } })
            }
          />
        </div>
        <div className="hint">
          Filename relative to <code>ComfyUI/models/loras/</code>, not an
          absolute path. Set <b>ComfyUI loras dir</b> in Config → services and
          training fills this in automatically; otherwise copy the file there
          and enter its name by hand. The trigger word is shared with the CG
          LoRA — they're two takes on one character, not two characters.
        </div>
        <Range
          label="Sprite LoRA weight"
          value={draft.assets.lora_weight}
          min={0.2}
          max={1.2}
          onChange={(v) =>
            save({ ...draft, assets: { ...draft.assets, lora_weight: v } })
          }
        />
      </CollapsibleCard>
    </>
  );
}

function SpritesTab({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  const store = useStore();
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set());
  // Just the default pose: poses multiply the sprite run, so opting into more
  // has to be deliberate.
  const [spritePoses, setSpritePoses] = useState<string[]>(["front"]);
  const [expressions, setExpressions] = useState<string[]>([
    "neutral",
    "smiling",
    "angry",
    "crying",
    "surprised",
  ]);

  // After a save that drops a hash from this character's record, best-effort
  // purge the file from disk and the Gallery too -- see SpriteLoraTab's copy
  // of this comment for the full reasoning.
  const purgeAsset = (hash: string) => {
    api.deleteGalleryAsset(store.project!.id, hash).catch(() => {});
  };

  const deleteSprite = async (slot: string) => {
    const hash = draft.assets.sprite_sheet[slot];
    await save({
      ...draft,
      assets: {
        ...draft.assets,
        sprite_sheet: Object.fromEntries(
          Object.entries(draft.assets.sprite_sheet).filter(([k]) => k !== slot),
        ),
      },
    });
    if (hash) purgeAsset(hash);
  };

  // Compiled-sprite lightbox: open one slot large, keep it or regenerate it.
  // The image is read live from sprite_sheet[slot], so it swaps itself in when
  // a regenerate job lands.
  const [spriteSlot, setSpriteSlot] = useState<string | null>(null);
  // Free-text prompt tweaks for a one-off regenerate; not saved to the
  // character, kept across slots so the same nudge can be applied to several.
  const [spriteExtraPos, setSpriteExtraPos] = useState("");
  const [spriteExtraNeg, setSpriteExtraNeg] = useState("");
  const spriteSlots = Object.keys(draft.assets.sprite_sheet);
  const spriteHash =
    spriteSlot && spriteSlots.includes(spriteSlot)
      ? draft.assets.sprite_sheet[spriteSlot]
      : null;
  const spriteBusy = spriteSlot ? regenerating.has(spriteSlot) : false;

  useEffect(() => {
    if (spriteSlot === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSpriteSlot(null);
      else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const idx = spriteSlots.indexOf(spriteSlot);
        if (idx === -1) return;
        const d = e.key === "ArrowRight" ? 1 : -1;
        setSpriteSlot(
          spriteSlots[(idx + d + spriteSlots.length) % spriteSlots.length],
        );
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spriteSlot, spriteSlots.join(" ")]);

  // Re-runs just one compiled slot -- same outfit/expression, a fresh seed --
  // for when one sprite came out wrong. Overwrites that slot in place once
  // the job lands (image.sprite always writes back to sprite_sheet[slot]).
  const regenerateSprite = async (
    slot: string,
    extra?: { positive?: string; negative?: string },
  ) => {
    // outfit/pose/expression, tolerating pre-pose two-part keys.
    const parts = slot.split("/");
    const [outfit_id, pose, expression] =
      parts.length >= 3 ? parts : [parts[0], "front", parts[1]];
    setRegenerating((prev) => new Set(prev).add(slot));
    try {
      const { job_id } = await api.generateSprite(store.project!.id, {
        character_id: draft.id,
        outfit_id,
        pose,
        expression,
        extra_positive: extra?.positive?.trim() ?? "",
        extra_negative: extra?.negative?.trim() ?? "",
      });
      store.refreshJobs();
      const poll = setInterval(async () => {
        const job = await api.getJob(job_id);
        if (job.status === "done") {
          clearInterval(poll);
          setRegenerating((prev) => {
            const next = new Set(prev);
            next.delete(slot);
            return next;
          });
          await store.refreshCharacters();
          store.notify("sprite regenerated");
        } else if (["error", "cancelled"].includes(job.status)) {
          clearInterval(poll);
          setRegenerating((prev) => {
            const next = new Set(prev);
            next.delete(slot);
            return next;
          });
          if (job.error) store.fail(job.error.split("\n")[0]);
        }
      }, 2000);
    } catch (e) {
      setRegenerating((prev) => {
        const next = new Set(prev);
        next.delete(slot);
        return next;
      });
      store.fail(String(e));
    }
  };

  return (
    <>
      <CollapsibleCard title="Sprites">
        <div className="hint" style={{ marginBottom: 8 }}>
          One whole sprite per outfit × pose × expression, background removed,
          declared in Ren'Py as attribute-tagged images. Didn't come out well?
          The ⟳ on a thumbnail below regenerates just that one slot with a fresh
          seed.
        </div>
        <div className="field">
          <label>Expressions</label>
          <div className="chip-grid">
            {(store.vocab?.expressions ?? []).map((expression) => (
              <span
                key={expression}
                className={`chip ${expressions.includes(expression) ? "on" : ""}`}
                onClick={() =>
                  setExpressions(
                    expressions.includes(expression)
                      ? expressions.filter((e) => e !== expression)
                      : [...expressions, expression],
                  )
                }
              >
                {expression}
              </span>
            ))}
          </div>
        </div>
        <div className="field">
          <label>Poses</label>
          <div className="chip-grid">
            {(store.vocab?.poses ?? []).map((pose) => (
              <span
                key={pose}
                className={`chip ${spritePoses.includes(pose) ? "on" : ""}`}
                onClick={() =>
                  setSpritePoses(
                    spritePoses.includes(pose)
                      ? spritePoses.filter((p) => p !== pose)
                      : [...spritePoses, pose],
                  )
                }
              >
                {pose}
              </span>
            ))}
          </div>
          <div className="hint">
            This is a product — every pose multiplies the run. Batch fill queues
            only the poses the script actually uses.
          </div>
        </div>
        <button
          className="primary"
          disabled={expressions.length === 0 || spritePoses.length === 0}
          onClick={async () => {
            const out = await api.generateSpriteSet(store.project!.id, {
              character_id: draft.id,
              expressions,
              poses: spritePoses,
            });
            store.notify(`${out.count} sprite jobs queued`);
            store.refreshJobs();
          }}
        >
          Generate{" "}
          {(draft.outfits.length || 1) *
            spritePoses.length *
            expressions.length}{" "}
          sprites
        </button>

        {Object.keys(draft.assets.sprite_sheet).length > 0 && (
          <div className="thumbs" style={{ marginTop: 10 }}>
            {Object.entries(draft.assets.sprite_sheet).map(([slot, hash]) => {
              const busy = regenerating.has(slot);
              return (
                <div key={slot} className="thumb">
                  <img
                    src={api.assetUrl(hash)}
                    loading="lazy"
                    title="Open in review lightbox"
                    style={busy ? { opacity: 0.4 } : undefined}
                    onClick={() => setSpriteSlot(slot)}
                  />
                  <button
                    className="regen"
                    title="Regenerate this sprite (same slot, new seed)"
                    disabled={busy}
                    onClick={() => regenerateSprite(slot)}
                  >
                    {busy ? "…" : "⟳"}
                  </button>
                  <button
                    className="del"
                    title="Delete this sprite slot"
                    disabled={busy}
                    onClick={() => deleteSprite(slot)}
                  >
                    ×
                  </button>
                  <div className="cap">{slot}</div>
                </div>
              );
            })}
          </div>
        )}

        {spriteSlot && spriteHash && (
          <div
            className="lightbox-backdrop"
            onClick={() => setSpriteSlot(null)}
          >
            <button
              className="lightbox-close"
              title="Close (Esc)"
              onClick={() => setSpriteSlot(null)}
            >
              ×
            </button>
            <div
              className="lightbox-stage"
              onClick={(e) => e.stopPropagation()}
              style={spriteBusy ? { opacity: 0.4 } : undefined}
            >
              <ZoomableImg
                className="lightbox-img"
                src={api.assetUrl(spriteHash)}
              />
            </div>
            <div className="lightbox-meta" onClick={(e) => e.stopPropagation()}>
              {spriteSlot}
              {spriteSlots.length > 1 && (
                <>
                  {" "}
                  · {spriteSlots.indexOf(spriteSlot) + 1} / {spriteSlots.length}
                  <br />← / → to step through slots
                </>
              )}
              <br />
              tap image to zoom
              {spriteBusy && (
                <>
                  <br />
                  regenerating…
                </>
              )}
            </div>
            <div
              className="lightbox-tweaks"
              onClick={(e) => e.stopPropagation()}
            >
              <input
                placeholder="Extra positive tags — e.g. hand on hip, windy"
                value={spriteExtraPos}
                disabled={spriteBusy}
                onChange={(e) => setSpriteExtraPos(e.target.value)}
              />
              <input
                placeholder="Extra negative tags — e.g. jacket, hat"
                value={spriteExtraNeg}
                disabled={spriteBusy}
                onChange={(e) => setSpriteExtraNeg(e.target.value)}
              />
              <span className="hint">
                Comma-separated booru tags, applied to this regenerate only —
                not saved to the character. Use <code>(tag:1.3)</code> to
                weight.
              </span>
            </div>
            <div
              className="lightbox-actions"
              onClick={(e) => e.stopPropagation()}
            >
              <button
                className="danger"
                disabled={spriteBusy}
                onClick={() => {
                  deleteSprite(spriteSlot);
                  setSpriteSlot(null);
                }}
              >
                Delete
              </button>
              <button
                disabled={spriteBusy}
                onClick={() =>
                  regenerateSprite(spriteSlot, {
                    positive: spriteExtraPos,
                    negative: spriteExtraNeg,
                  })
                }
              >
                {spriteBusy ? "Regenerating…" : "Regenerate"}
              </button>
              <button className="primary" onClick={() => setSpriteSlot(null)}>
                Keep
              </button>
            </div>
          </div>
        )}
      </CollapsibleCard>
    </>
  );
}

/**
 * The CG LoRA's own bootstrap loop -- shoot a varied dataset with the Sprite
 * LoRA, cull it, caption it, train. Deliberately the same shape as the
 * Sprites tab (generate → cull → caption/train), just pointed at cg_pool and
 * lora_cg instead of sheet_pool and lora, because it is the same loop with a
 * different dataset, not a different idea.
 */
function CgLoraTab({
  draft,
  save,
}: {
  draft: Character;
  save: (c: Character) => void;
}) {
  const store = useStore();
  const pool = draft.assets.cg_pool;
  const uncaptioned = pool.filter((p) => !p.caption);

  const [excludedOutfits, setExcludedOutfits] = useState<Set<string>>(
    new Set(),
  );
  const [excludedLocations, setExcludedLocations] = useState<Set<string>>(
    new Set(),
  );
  const [angles, setAngles] = useState<string[]>(SHEET_ANGLES);
  const [expressions, setExpressions] = useState<string[]>([
    "neutral",
    "smiling",
    "angry",
    "crying",
    "surprised",
  ]);
  const [times, setTimes] = useState<string[]>([
    "day",
    "evening",
    "night",
    "morning",
  ]);
  const [count, setCount] = useState(24);
  // Empty = the whole vocabulary (the backend samples from its own defaults),
  // which is the right starting point: these are exactly the axes the sheet
  // could not teach, so narrowing them should be deliberate.
  const [actions, setActions] = useState<string[]>([]);
  const [company, setCompany] = useState<string[]>([]);
  const [generating, setGenerating] = useState(false);
  const [captioning, setCaptioning] = useState(false);
  const [poolRegen, setPoolRegen] = useState<Set<string>>(new Set());
  const [confirmClearPool, setConfirmClearPool] = useState(false);

  const selectedOutfits = draft.outfits.filter(
    (o) => !excludedOutfits.has(o.id),
  );
  const noOutfits = draft.outfits.length === 0;
  const outfitIds = noOutfits ? [] : selectedOutfits.map((o) => o.id);

  const noLocations = store.locations.length === 0;
  const selectedLocations = store.locations.filter(
    (l) => !excludedLocations.has(l.id),
  );
  const locationIds = noLocations ? [] : selectedLocations.map((l) => l.id);

  const canGenerate =
    angles.length > 0 &&
    expressions.length > 0 &&
    times.length > 0 &&
    (noOutfits || selectedOutfits.length > 0) &&
    count > 0;

  // Same purge contract as the Sprites tab's cull card: the backend refuses
  // to delete anything a character record still references, so this only
  // ever runs after the save that drops the hash has resolved.
  const purgeAsset = (hash: string) => {
    api.deleteGalleryAsset(store.project!.id, hash).catch(() => {});
  };

  const deleteItem = async (hash: string) => {
    await save({
      ...draft,
      assets: { ...draft.assets, cg_pool: pool.filter((p) => p.hash !== hash) },
    });
    purgeAsset(hash);
  };

  // Regenerate one pool image: pin every field of image.cg_set to this one
  // item's exact recipe (a single-element list is the same as "always this
  // value" to the handler's cycling) so only the seed varies, then drop the
  // old image now -- the replacement lands at the end of the pool when the
  // job finishes.
  const regeneratePoolItem = async (item: SheetPoolItem) => {
    setPoolRegen((prev) => new Set(prev).add(item.hash));
    const clear = () =>
      setPoolRegen((prev) => {
        const next = new Set(prev);
        next.delete(item.hash);
        return next;
      });
    try {
      const { job_id } = await api.generateCgSet(store.project!.id, {
        character_id: draft.id,
        count: 1,
        outfit_ids: [item.outfit_id],
        angles: [item.angle],
        expressions: [item.expression],
        // Every axis pinned to this item's own recipe, so only the seed
        // varies. Older pool items predate action/company and have them
        // empty; an empty list means "sample freely", which is the right
        // fallback there.
        actions: item.action ? [item.action] : [],
        company: item.company ? [item.company] : [],
        framings: [item.framing],
        scenes: [item.scene],
      });
      await save({
        ...draft,
        assets: {
          ...draft.assets,
          cg_pool: pool.filter((p) => p.hash !== item.hash),
        },
      });
      purgeAsset(item.hash);
      store.notify("regenerating pool image");
      store.refreshJobs();
      const poll = setInterval(async () => {
        const job = await api.getJob(job_id);
        await store.refreshCharacters();
        if (job.status === "done") {
          clearInterval(poll);
          clear();
          store.notify("pool image regenerated");
        } else if (["error", "cancelled"].includes(job.status)) {
          clearInterval(poll);
          clear();
          if (job.error) store.fail(job.error.split("\n")[0]);
        }
      }, 2000);
    } catch (e) {
      clear();
      store.fail(String(e));
    }
  };

  // Full-screen cull: identical UI to the Sprites tab's, its own state --
  // the two pools are culled independently.
  const [cullIndex, setCullIndex] = useState<number | null>(null);
  const [swipe, setSwipe] = useState(0);
  const cullItem = cullIndex !== null ? pool[cullIndex] : null;

  const closeCull = () => {
    setSwipe(0);
    setCullIndex(null);
  };
  const cullKeep = () => {
    setSwipe(0);
    setCullIndex((i) => (i === null || i + 1 >= pool.length ? null : i + 1));
  };
  const cullDelete = () => {
    if (cullIndex === null) return;
    const nextPool = pool.filter((p) => p.hash !== pool[cullIndex].hash);
    deleteItem(pool[cullIndex].hash);
    setSwipe(0);
    setCullIndex((i) => (i === null || i >= nextPool.length ? null : i));
  };
  const cullRegenerate = () => {
    if (cullIndex === null) return;
    const nextLen = pool.length - 1;
    regeneratePoolItem(pool[cullIndex]);
    setSwipe(0);
    setCullIndex((i) => (i === null || i >= nextLen ? null : i));
  };

  useEffect(() => {
    if (cullIndex === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeCull();
      else if (e.key === "ArrowRight") cullKeep();
      else if (e.key === "ArrowLeft") cullDelete();
      else if (e.key.toLowerCase() === "r") cullRegenerate();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cullIndex, pool]);

  return (
    <>
      <CollapsibleCard title="1 · CG training set">
        <div className="hint" style={{ marginBottom: 8 }}>
          Shot with the Sprite LoRA, but away from the studio backdrop: real
          project locations, varied angles, framings and light. Generates new
          images and adds them to the pool below — it never replaces what's
          already there. This is what teaches a second LoRA that the character
          isn't just a front-facing figure on white.
        </div>
        {!noOutfits && (
          <div className="field">
            <label>Outfits</label>
            <div className="chip-grid">
              {draft.outfits.map((o) => {
                const on = !excludedOutfits.has(o.id);
                return (
                  <span
                    key={o.id}
                    className={`chip ${on ? "on" : ""}`}
                    onClick={() => {
                      const next = new Set(excludedOutfits);
                      if (on) next.add(o.id);
                      else next.delete(o.id);
                      setExcludedOutfits(next);
                    }}
                  >
                    {o.name}
                  </span>
                );
              })}
            </div>
          </div>
        )}
        {!noLocations && (
          <div className="field">
            <label>Scenes (locations)</label>
            <div className="chip-grid">
              {store.locations.map((l) => {
                const on = !excludedLocations.has(l.id);
                return (
                  <span
                    key={l.id}
                    className={`chip ${on ? "on" : ""}`}
                    onClick={() => {
                      const next = new Set(excludedLocations);
                      if (on) next.add(l.id);
                      else next.delete(l.id);
                      setExcludedLocations(next);
                    }}
                  >
                    {l.name}
                  </span>
                );
              })}
            </div>
          </div>
        )}
        <div className="field">
          <label>Angles</label>
          <div className="chip-grid">
            {SHEET_ANGLES.map((angle) => (
              <span
                key={angle}
                className={`chip ${angles.includes(angle) ? "on" : ""}`}
                onClick={() =>
                  setAngles(
                    angles.includes(angle)
                      ? angles.filter((a) => a !== angle)
                      : [...angles, angle],
                  )
                }
              >
                {angle.split(",")[0]}
              </span>
            ))}
          </div>
        </div>
        <div className="field">
          <label>Expressions</label>
          <div className="chip-grid">
            {(store.vocab?.expressions ?? []).map((expression) => (
              <span
                key={expression}
                className={`chip ${expressions.includes(expression) ? "on" : ""}`}
                onClick={() =>
                  setExpressions(
                    expressions.includes(expression)
                      ? expressions.filter((e) => e !== expression)
                      : [...expressions, expression],
                  )
                }
              >
                {expression}
              </span>
            ))}
          </div>
        </div>
        <div className="field">
          <label>Times of day</label>
          <div className="chip-grid">
            {(store.vocab?.time_of_day ?? []).map((t) => (
              <span
                key={t}
                className={`chip ${times.includes(t) ? "on" : ""}`}
                onClick={() =>
                  setTimes(
                    times.includes(t)
                      ? times.filter((x) => x !== t)
                      : [...times, t],
                  )
                }
              >
                {t}
              </span>
            ))}
          </div>
        </div>
        <div className="field">
          <label>
            Actions <span className="hint">none selected = all of them</span>
          </label>
          <div className="chip-grid">
            {(store.vocab?.cg_set_actions ?? []).map((a) => (
              <span
                key={a}
                className={`chip ${actions.includes(a) ? "on" : ""}`}
                onClick={() =>
                  setActions(
                    actions.includes(a)
                      ? actions.filter((x) => x !== a)
                      : [...actions, a],
                  )
                }
              >
                {a}
              </span>
            ))}
          </div>
          <div className="hint">
            An event CG prompts on context and action, not a pose enum — this is
            what teaches the LoRA that its subject's body moves.
          </div>
        </div>
        <div className="field">
          <label>
            Who else is in frame{" "}
            <span className="hint">none selected = all of them</span>
          </label>
          <div className="chip-grid">
            {(store.vocab?.cg_set_company ?? []).map((c) => (
              <span
                key={c}
                className={`chip ${company.includes(c) ? "on" : ""}`}
                onClick={() =>
                  setCompany(
                    company.includes(c)
                      ? company.filter((x) => x !== c)
                      : [...company, c],
                  )
                }
              >
                {c}
              </span>
            ))}
          </div>
          <div className="hint">
            A CG is shot from the player's point of view, so someone is often in
            frame or right against the camera. Trained only on solo studio
            shots, a LoRA fights every two-hander and every close POV moment.
            The second figure is <b>faceless</b> deliberately — a real second
            face is a second identity for the LoRA to learn and then leak into
            your character.
          </div>
        </div>
        <div className="grid2">
          <Num
            label="Count"
            step={1}
            value={count}
            onChange={(v) => setCount(Math.max(1, v))}
          />
        </div>
        <div className="hint">
          Outfit, scene, angle, expression, action, company and framing are each
          sampled on their own per image — this is not a product of every
          combination, just {count} independently varied shots.
        </div>
        <button
          className="primary"
          disabled={!canGenerate || generating}
          onClick={async () => {
            try {
              setGenerating(true);
              const { job_id } = await api.generateCgSet(store.project!.id, {
                character_id: draft.id,
                count,
                outfit_ids: outfitIds,
                angles,
                expressions,
                times,
                actions,
                company,
                location_ids: locationIds,
              });
              store.notify(`CG training set queued — ${count} image(s)`);
              store.refreshJobs();
              const poll = setInterval(async () => {
                const job = await api.getJob(job_id);
                await store.refreshCharacters();
                if (job.status === "done") {
                  clearInterval(poll);
                  setGenerating(false);
                } else if (["error", "cancelled"].includes(job.status)) {
                  clearInterval(poll);
                  setGenerating(false);
                  if (job.error) store.fail(job.error.split("\n")[0]);
                }
              }, 3000);
            } catch (e) {
              setGenerating(false);
              store.fail(String(e));
            }
          }}
        >
          {generating ? "Generating…" : `Generate ${count} image(s)`}
        </button>
      </CollapsibleCard>

      {pool.length > 0 && (
        <CollapsibleCard
          title={`2 · Cull (${pool.length} in pool)`}
          headExtra={
            <button
              className="tiny danger"
              onClick={() => setConfirmClearPool(true)}
            >
              Clear all
            </button>
          }
        >
          <div className="hint" style={{ marginBottom: 8 }}>
            Delete the ones that don't agree with each other — whatever's left
            here is what training uses next, no picking required. Click an image
            to review the pool one at a time.
          </div>
          <div className="thumbs">
            {pool.map((item, i) => (
              <div key={item.hash} className="thumb">
                <img
                  src={api.assetUrl(item.hash)}
                  loading="lazy"
                  title="Open in review lightbox"
                  onClick={() => {
                    setSwipe(0);
                    setCullIndex(i);
                  }}
                />
                <button
                  className="regen"
                  title="Regenerate this image (new seed, same recipe)"
                  disabled={poolRegen.has(item.hash)}
                  onClick={() => regeneratePoolItem(item)}
                >
                  {poolRegen.has(item.hash) ? "…" : "⟳"}
                </button>
                <button
                  className="del"
                  title="Remove from the pool"
                  onClick={() => deleteItem(item.hash)}
                >
                  ×
                </button>
                <div className={`cap ${item.caption ? "captioned" : ""}`}>
                  {draft.outfits.find((o) => o.id === item.outfit_id)?.name ??
                    "default"}{" "}
                  · {item.expression} · {item.angle.split(",")[0]}
                </div>
              </div>
            ))}
          </div>
        </CollapsibleCard>
      )}

      {confirmClearPool && (
        <ConfirmDialog
          title={`Clear all ${pool.length} pool image(s)?`}
          confirmLabel="Clear pool"
          onCancel={() => setConfirmClearPool(false)}
          onConfirm={() => {
            setConfirmClearPool(false);
            save({ ...draft, assets: { ...draft.assets, cg_pool: [] } });
          }}
        >
          <div className="hint">
            The images stay in the Gallery; only the pool reference goes away.
            If you've already trained the CG LoRA on this pool, clearing it
            doesn't touch that LoRA — only what a future retrain would use.
          </div>
        </ConfirmDialog>
      )}

      {cullItem && cullIndex !== null && (
        <div className="lightbox-backdrop" onClick={closeCull}>
          <button
            className="lightbox-close"
            title="Close (Esc)"
            onClick={closeCull}
          >
            ×
          </button>
          <div className="lightbox-stage" onClick={(e) => e.stopPropagation()}>
            <span
              className="lightbox-verdict del"
              style={{ opacity: swipe < -20 ? Math.min(1, -swipe / 90) : 0 }}
            >
              Delete
            </span>
            <span
              className="lightbox-verdict keep"
              style={{ opacity: swipe > 20 ? Math.min(1, swipe / 90) : 0 }}
            >
              Keep
            </span>
            <ZoomableImg
              className="lightbox-img"
              src={api.assetUrl(cullItem.hash)}
              onSwipeProgress={setSwipe}
              onSwipeLeft={cullDelete}
              onSwipeRight={cullKeep}
            />
          </div>
          <div className="lightbox-meta" onClick={(e) => e.stopPropagation()}>
            {cullIndex + 1} / {pool.length} ·{" "}
            {draft.outfits.find((o) => o.id === cullItem.outfit_id)?.name ??
              "default"}{" "}
            · {cullItem.expression} · {cullItem.angle.split(",")[0]}
            {cullItem.scene && <> · {cullItem.scene.split(",")[0]}</>}
            <br />→ keep · ← delete · R regenerate · tap image to zoom
          </div>
          <div
            className="lightbox-actions"
            onClick={(e) => e.stopPropagation()}
          >
            <button className="danger" onClick={cullDelete}>
              Delete
            </button>
            <button onClick={cullRegenerate}>Regenerate</button>
            <button className="primary" onClick={cullKeep}>
              Keep
            </button>
          </div>
        </div>
      )}

      <CollapsibleCard title="3 · Caption and train">
        <div className="row" style={{ gap: 8 }}>
          <button
            disabled={uncaptioned.length === 0 || captioning}
            onClick={async () => {
              try {
                setCaptioning(true);
                const { job_id } = await api.captionImages(
                  store.project!.id,
                  draft.id,
                  uncaptioned.map((p) => p.hash),
                  "cg",
                );
                store.notify(`captioning ${uncaptioned.length}`);
                store.refreshJobs();
                const poll = setInterval(async () => {
                  const job = await api.getJob(job_id);
                  await store.refreshCharacters();
                  if (job.status === "done") {
                    clearInterval(poll);
                    setCaptioning(false);
                    store.notify("captions ready");
                  } else if (["error", "cancelled"].includes(job.status)) {
                    clearInterval(poll);
                    setCaptioning(false);
                    if (job.error) store.fail(job.error.split("\n")[0]);
                  }
                }, 2000);
              } catch (e) {
                setCaptioning(false);
                store.fail(String(e));
              }
            }}
          >
            {captioning
              ? "Captioning…"
              : `Auto-caption (${uncaptioned.length} new)`}
          </button>
          <button
            className="primary"
            disabled={pool.length < 8}
            onClick={async () => {
              await api.trainLora(store.project!.id, {
                character_id: draft.id,
                slot: "cg",
              });
              store.notify(
                "CG LoRA training queued — this takes 20-40 min on a 4090",
              );
              store.refreshJobs();
            }}
          >
            Train CG LoRA ({pool.length})
          </button>
        </div>
        <div className="hint">
          Trains on this pool only — the Sprite LoRA and its sheet are untouched
          either way. An event CG uses this LoRA once it exists (Config → Event
          CG character LoRA, or per node in the inspector), at the weights below
          rather than the Sprite LoRA's.
        </div>
        {pool.length > 0 && pool.length < 8 && (
          <div className="hint warn">
            15–30 varied images is the working range. Quality matters far more
            than count.
          </div>
        )}
        {pool.length >= 8 && uncaptioned.length > 0 && (
          <div className="hint warn">
            {uncaptioned.length} image(s) not captioned yet — auto-caption first
            for better results.
          </div>
        )}
        <div className="grid2" style={{ marginTop: 8 }}>
          <Text
            label="CG LoRA path"
            value={draft.assets.lora_cg ?? ""}
            onChange={(v) =>
              save({ ...draft, assets: { ...draft.assets, lora_cg: v } })
            }
          />
          <div className="field">
            <label>
              Trigger word <span className="hint">set on the Sprites tab</span>
            </label>
            <input value={draft.assets.trigger} readOnly disabled />
          </div>
        </div>
        <div className="hint">
          Filename relative to <code>ComfyUI/models/loras/</code>, same as the
          Sprite LoRA. The trigger word is shared and not editable here on
          purpose, so the two LoRAs can never drift onto different trigger words
          for the same character.
        </div>
        <div className="grid2">
          <Range
            label="CG weight"
            value={draft.assets.cg_lora_weight}
            min={0.2}
            max={1.2}
            onChange={(v) =>
              save({ ...draft, assets: { ...draft.assets, cg_lora_weight: v } })
            }
          />
          <Range
            label="Face-pass weight"
            value={draft.assets.face_lora_weight}
            min={0.2}
            max={1.2}
            onChange={(v) =>
              save({
                ...draft,
                assets: { ...draft.assets, face_lora_weight: v },
              })
            }
          />
        </div>
        <div className="hint">
          An event CG runs whichever LoRA it loads at <b>CG weight</b> — lower
          than the Sprite LoRA's own weight, so the pose stays free of the
          sheet's front-facing prior — and then re-renders just the face crop at{" "}
          <b>face-pass weight</b>, which is where the likeness comes back. The
          face pass runs even when the CG loads no LoRA at all.
        </div>
      </CollapsibleCard>
    </>
  );
}

function PromptPreviewCard({ character }: { character: Character }) {
  const store = useStore();
  const [preview, setPreview] = useState<any>(null);
  const [expression, setExpression] = useState("neutral");
  const [outfit, setOutfit] = useState(character.default_outfit);
  const [testing, setTesting] = useState(false);
  const [testHash, setTestHash] = useState<string | null>(null);

  useEffect(() => {
    api
      .promptPreview(store.project!.id, character.id, outfit, expression)
      .then(setPreview)
      .catch(() => setPreview(null));
    setTestHash(null);
  }, [character, outfit, expression]);

  return (
    <CollapsibleCard title="Image prompt preview">
      <div className="grid2">
        <Select
          label="Outfit"
          value={outfit}
          options={character.outfits.map(
            (o) => [o.id, o.name] as [string, string],
          )}
          onChange={setOutfit}
        />
        <Select
          label="Expression"
          value={expression}
          options={(store.vocab?.expressions ?? []).map(
            (e) => [e, e] as [string, string],
          )}
          onChange={setExpression}
        />
      </div>
      {preview && (
        <>
          <div className="field">
            <label>Positive</label>
            <pre className="script" style={{ maxHeight: 130 }}>
              {preview.positive}
            </pre>
          </div>
          <div className="field">
            <label>Negative</label>
            <pre className="script" style={{ maxHeight: 80 }}>
              {preview.negative}
            </pre>
          </div>
          <div className="hint">
            The locked identity block is{" "}
            <code>{preview.identity_tags || "(empty)"}</code>. Generation always
            uses this, never LLM-written identity tags.
          </div>
        </>
      )}
      <button
        className="primary"
        disabled={testing || character.age < 18}
        title={
          character.age < 18
            ? "Under 18 — blocked from the image pipeline"
            : "Runs this exact prompt through ComfyUI now"
        }
        style={{ marginTop: 8 }}
        onClick={async () => {
          try {
            setTesting(true);
            setTestHash(null);
            const { job_id } = await api.generateSprite(store.project!.id, {
              character_id: character.id,
              outfit_id: outfit,
              expression,
            });
            store.notify("generating test image…");
            store.refreshJobs();
            const poll = setInterval(async () => {
              const job = await api.getJob(job_id);
              if (job.status === "done") {
                clearInterval(poll);
                setTesting(false);
                setTestHash(job.result.hash ?? null);
              } else if (["error", "cancelled"].includes(job.status)) {
                clearInterval(poll);
                setTesting(false);
                if (job.error) store.fail(job.error.split("\n")[0]);
              }
            }, 1500);
          } catch (e) {
            setTesting(false);
            store.fail(String(e));
          }
        }}
      >
        {testing ? "Generating…" : "Generate test image"}
      </button>
      <div className="hint">
        Runs one sprite through ComfyUI with the prompt above so you can see how
        the character actually looks. Fills this outfit/expression slot in the
        sprite sheet — the same image the compiled game would use, not a
        throwaway preview.
      </div>
      {testHash && (
        <img
          src={api.assetUrl(testHash)}
          style={{
            maxWidth: 220,
            marginTop: 8,
            borderRadius: 6,
            border: "2px solid var(--line)",
          }}
        />
      )}
    </CollapsibleCard>
  );
}
