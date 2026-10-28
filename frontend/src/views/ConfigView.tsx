import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import { Check, Num, Select, Text } from "../components/Inspector";
import { useStore } from "../lib/store";

type Tab = "services" | "templates" | "sampling" | "workflows" | "emotion";

export default function ConfigView() {
  const [tab, setTab] = useState<Tab>("services");
  return (
    <div className="view">
      <h2>Configuration</h2>
      <div className="tabs" style={{ width: "fit-content", marginBottom: 12 }}>
        {(["services", "templates", "sampling", "workflows", "emotion"] as Tab[]).map((t) => (
          <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      {tab === "services" && <ServicesTab />}
      {tab === "templates" && <TemplatesTab />}
      {tab === "sampling" && <SamplingTab />}
      {tab === "workflows" && <WorkflowsTab />}
      {tab === "emotion" && <EmotionTab />}
    </div>
  );
}

function ServicesTab() {
  const store = useStore();
  const [settings, setSettings] = useState<Record<string, any>>({});
  const [health, setHealth] = useState<any>(null);
  const [checking, setChecking] = useState(false);
  const [models, setModels] = useState<string[]>([]);

  useEffect(() => { api.getSettings().then(setSettings); }, []);
  // Populated on mount so the three model selects are usable without first
  // running the full service sweep. An unreachable server just leaves the
  // list empty and the fields fall back to free text.
  useEffect(() => { api.llmModels().then((r) => setModels(r.models ?? [])); }, []);

  const check = async () => {
    setChecking(true);
    try {
      setHealth(await api.serviceHealth());
    } finally {
      setChecking(false);
    }
  };

  const save = async () => {
    await api.putSettings(settings);
    store.notify("settings saved");
  };

  const field = (key: string, label: string, hint?: string) => (
    <div className="field">
      <label>{label}</label>
      <input value={settings[key] ?? ""}
        onChange={(e) => setSettings({ ...settings, [key]: e.target.value })} />
      {hint && <div className="hint">{hint}</div>}
    </div>
  );

  /**
   * A model field backed by what LM Studio actually has. Stays a text input
   * when the list is empty (server down, or a model not loaded yet) rather
   * than trapping you in a select with nothing in it -- and keeps a value that
   * is no longer in the list selectable, so a stale setting is visible instead
   * of silently resetting to blank.
   */
  const modelField = (key: string, label: string, hint?: string) => {
    const value = settings[key] ?? "";
    const options = models.includes(value) || !value ? models : [value, ...models];
    return (
      <div className="field">
        <label>{label}</label>
        {models.length ? (
          <select value={value}
            onChange={(e) => setSettings({ ...settings, [key]: e.target.value })}>
            <option value="">— whatever LM Studio has loaded —</option>
            {options.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        ) : (
          <input value={value} placeholder="LM Studio unreachable — type an id"
            onChange={(e) => setSettings({ ...settings, [key]: e.target.value })} />
        )}
        {hint && <div className="hint">{hint}</div>}
      </div>
    );
  };

  return (
    <>
      <div className="card">
        <div className="card-head">
          <span className="name">Endpoints</span>
          <span style={{ marginLeft: "auto" }} />
          <button title="Pings each configured service and reports which are reachable. Changes nothing."
            onClick={check} disabled={checking}>{checking ? "checking…" : "Check services"}</button>
          <button className="primary" onClick={save}>Save</button>
        </div>
        {field("lm_studio_url", "LM Studio URL", "OpenAI-compatible /v1 endpoint")}
        {field("comfyui_url", "ComfyUI URL",
          "ComfyUI has no authentication — bind it to LAN only, never expose it")}
        {field("renpy_sdk_path", "Ren'Py SDK path", "Needed to launch or lint the built game")}
        {field("kohya_path", "kohya_ss path", "Needed for LoRA training")}
        {field("lora_base_model", "LoRA base checkpoint", "SDXL base used for training")}
        {field("comfyui_lora_dir", "ComfyUI loras dir",
          "Path to ComfyUI/models/loras/ — set it and a trained LoRA is wired up automatically, no manual copy")}
        {field("ffmpeg_path", "ffmpeg path", "Used to encode voice lines to Ogg Vorbis")}
      </div>

      {health && (
        <div className="card">
          <div className="card-head"><span className="name">Status</span></div>
          <table className="data">
            <tbody>
              <tr>
                <th>LM Studio</th>
                <td>
                  <span className={`pill ${health.lm_studio.ok ? "good" : "bad"}`}>
                    {health.lm_studio.ok ? "up" : "down"}
                  </span>{" "}
                  {health.lm_studio.ok
                    ? `${health.lm_studio.models.length} models`
                    : health.lm_studio.error}
                </td>
              </tr>
              <tr>
                <th>ComfyUI</th>
                <td>
                  <span className={`pill ${health.comfyui.ok ? "good" : "bad"}`}>
                    {health.comfyui.ok ? "up" : "down"}
                  </span>{" "}
                  {health.comfyui.ok
                    ? `${health.checkpoints.length} checkpoints, ${health.loras.length} LoRAs`
                    : health.comfyui.error}
                </td>
              </tr>
              <tr>
                <th>Assets on disk</th>
                <td>{(health.assets.bytes / 1e6).toFixed(1)} MB</td>
              </tr>
            </tbody>
          </table>
          <div className="row" style={{ marginTop: 8 }}>
            <button title="Tells ComfyUI to unload its models so the LLM or TTS can have the card. Safe at any time; the next image job reloads them."
              onClick={() => api.freeVram().then(() => store.notify("VRAM freed"))}>
              Free ComfyUI VRAM
            </button>
            <button title="Tells LM Studio to unload every resident model so ComfyUI can have the card. Needs the lms CLI on PATH; the next writing job reloads the model."
              onClick={() => api.unloadLlm().then((r) =>
                r.unloaded ? store.notify("LLM unloaded") : store.fail(r.note))}>
              Unload LLM
            </button>
            <button title="Cancels the image or voice job ComfyUI is running right now. The queue moves on to the next one."
              onClick={() => api.interrupt().then(() => store.notify("interrupted"))}>
              Interrupt current ComfyUI job
            </button>
          </div>
          {health.lm_studio.ok && !health.lm_studio.can_unload && (
            <div className="hint warn" style={{ marginTop: 6 }}>
              ⚠ `lms` is not on PATH, so the LLM cannot be unloaded automatically
              — run `lms bootstrap` once, or set the CLI path below. Until then a
              VRAM swap can free ComfyUI but not LM Studio, and image jobs will
              still be fighting the loaded model for the card.
            </div>
          )}
          <div className="hint" style={{ marginTop: 6 }}>
            One 24 GB GPU cannot hold an LLM, SDXL and TTS at once. GPU jobs are
            serialized behind a single lock by default, and with VRAM swapping on
            (below) whichever engine is holding the card is unloaded before a job
            that needs the other one.
          </div>
        </div>
      )}

      <TagDictionaryCard />

      <div className="card">
        <div className="card-head"><span className="name">Models and generation</span>
          <span style={{ marginLeft: "auto" }} />
          <button className="primary" onClick={save}>Save</button>
        </div>
        {modelField("lm_studio_prose_model", "Prose model",
          "A creative fine-tune belongs here — Cydonia 24B is the default recommendation.")}
        {modelField("lm_studio_struct_model", "Structuring model",
          "Segmentation only: it copies text verbatim rather than improving it, so a "
          + "strict instruct model beats a creative tune. Qwen2.5-14B-Instruct at 8k "
          + "context co-resides with a 24B prose model on 24 GB, so no swapping.")}
        {field("image_checkpoint", "SDXL checkpoint")}
        <div className="grid3">
          <NumSetting settings={settings} setSettings={setSettings} k="image_steps" label="Steps" step={1} />
          <NumSetting settings={settings} setSettings={setSettings} k="image_cfg" label="CFG" step={0.5} />
          <div className="field">
            <label>Scheduler</label>
            <input value={settings.image_scheduler ?? ""}
              onChange={(e) => setSettings({ ...settings, image_scheduler: e.target.value })} />
          </div>
        </div>
        <div className="hint">
          NoobAI-XL V-Pred needs euler, CFG 4–5, ~28 steps and <strong>no karras</strong>
          {" "}scheduler — karras produces artifacts in v-prediction mode.
        </div>
        <div className="grid2">
          <NumSetting settings={settings} setSettings={setSettings} k="image_width" label="Sprite width" step={64} />
          <NumSetting settings={settings} setSettings={setSettings} k="image_height" label="Sprite height" step={64} />
        </div>
        <div className="grid2">
          <NumSetting settings={settings} setSettings={setSettings} k="cg_width" label="CG width" step={64} />
          <NumSetting settings={settings} setSettings={setSettings} k="cg_height" label="CG height" step={64} />
        </div>
        <div className="grid2">
          <div className="field">
            <label>Event CG character LoRA</label>
            <select value={settings.cg_lora_mode ?? "auto"}
              onChange={(e) => setSettings({ ...settings, cg_lora_mode: e.target.value })}>
              <option value="auto">auto — CG LoRA if trained, else Sprite, else none</option>
              <option value="none">none — tags + face pass only</option>
              <option value="sprite">Sprite LoRA</option>
              <option value="cg">CG LoRA</option>
            </select>
          </div>
          <NumSetting settings={settings} setSettings={setSettings} k="cg_face_denoise"
            label="Face-pass denoise" step={0.05} />
        </div>
        <div className="grid2">
          <NumSetting settings={settings} setSettings={setSettings} k="cg_batch"
            label="CG candidates per run" step={1} />
          <NumSetting settings={settings} setSettings={setSettings} k="cg_tag_min_posts"
            label="Minimum posts per tag" step={50} />
        </div>
        <div className="field">
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={settings.cg_tag_weights !== false}
              onChange={(e) => setSettings({ ...settings, cg_tag_weights: e.target.checked })} />
            <span>Weight CG tags by what they are — <code>(hug:1.15)</code></span>
          </label>
        </div>
        <div className="hint">
          Emphasis is assigned from the tag's own category, never written by
          the model: what the bodies are doing goes up, the room and the light
          step back, and identity is never touched. <b>Minimum posts</b> is the
          floor under a tag the director spends — below a few hundred Danbooru
          posts the checkpoint has seen a handful of images, which is
          indistinguishable from noise. It matches the tag import's own
          threshold, so it only bites if you have fetched a wider dictionary.
        </div>
        <div className="hint">
          How many images one CG render samples, kept as takes on the node so
          you pick one from the strip. The inspector's ×1/×2/×4 picker writes
          this same value; set it here to go past 4 (the ceiling is 8). Only
          the interactive path reads it — an overnight fill and the stale sweep
          render one per node, because nobody is there to choose and four times
          the images is four times the hours.
        </div>
        <div className="field">
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={settings.cg_face_detail !== false}
              onChange={(e) => setSettings({ ...settings, cg_face_detail: e.target.checked })} />
            <span>Re-render the face crop on event CGs (FaceDetailer)</span>
          </label>
        </div>
        <div className="hint">
          A CG loads the character's LoRA by default (auto), at the lower CG
          weight set on the character — a character with no LoRA trained yet
          just renders without one instead of blocking. The face pass is what
          keeps the composition from getting dragged back to the sheet's
          front-facing standing shot even so, by re-rendering only the
          detected face at the character's face-pass weight. Needs
          <b>ComfyUI-Impact-Pack</b> (FaceDetailer + UltralyticsDetectorProvider)
          and a face bbox model in <code>models/ultralytics/bbox/</code>;
          without them the CG still
          renders and the job says the pass was skipped. Denoise 0.4–0.5 is the
          working range — higher redraws a face that no longer matches the
          body's light and angle.
        </div>
        <div className="grid2">
          <NumSetting settings={settings} setSettings={setSettings} k="verbatim_token_budget"
            label="Verbatim context budget" step={250} />
          <NumSetting settings={settings} setSettings={setSettings} k="tts_emo_alpha"
            label="TTS emo_alpha (max 0.6)" step={0.05} />
          <NumSetting settings={settings} setSettings={setSettings} k="voice_retakes"
            label="Takes per voice re-take" step={1} />
        </div>
        <div className="field">
          <label className="row" style={{ gap: 6 }}>
            <input type="checkbox" checked={settings.gpu_serialize !== false}
              onChange={(e) => setSettings({ ...settings, gpu_serialize: e.target.checked })} />
            <span>Serialize GPU jobs (one at a time)</span>
          </label>
          <div className="hint">
            Also what keeps the queue in order: GPU jobs are claimed one at a
            time, so same-engine work stays contiguous and the VRAM swap below
            fires at real boundaries rather than wherever four jobs happened to
            race. Compiling, exporting and batch fan-out still run alongside.
          </div>
          <label className="row" style={{ gap: 6, marginTop: 6 }}>
            <input type="checkbox" checked={settings.vram_exclusive !== false}
              onChange={(e) => setSettings({ ...settings, vram_exclusive: e.target.checked })} />
            <span>Swap VRAM between the LLM and ComfyUI automatically</span>
          </label>
          <div className="hint">
            Serializing stops two jobs <em>running</em> at once; it does nothing
            about LM Studio and ComfyUI both sitting on dedicated VRAM while
            idle, which is what makes one card refuse the second engine. With
            this on, whoever holds the card is unloaded before a job that needs
            the other, and the LLM is loaded back — measured at about 8s for a
            24B — before its prompt is sent. Queue whatever you like in any
            order; a run of renders pays for one swap, not one each. Turn it off
            if you have a second GPU.
          </div>
        </div>
        <div className="field">
          <label>Path to the `lms` CLI</label>
          <input value={settings.lms_cli_path ?? ""}
            placeholder="blank = find `lms` on PATH"
            onChange={(e) => setSettings({ ...settings, lms_cli_path: e.target.value })} />
          <div className="hint">
            Only used to unload the LLM — LM Studio's OpenAI-compatible API has
            no way to do it. `lms bootstrap` puts the CLI on PATH, which is all
            most installs need.
          </div>
        </div>

        <div className="section-title">Writing</div>
        <div className="field">
          <label>Length of one continuation</label>
          <input value={settings.prose_beat_target ?? ""}
            onChange={(e) => setSettings({ ...settings, prose_beat_target: e.target.value })} />
          <div className="hint">
            Small local models start recapping past roughly this much — raise it
            only if yours holds together.
          </div>
        </div>
        <div className="field">
          <label>Player character policy</label>
          <textarea rows={4} value={settings.prose_pc_policy ?? ""}
            onChange={(e) => setSettings({ ...settings, prose_pc_policy: e.target.value })} />
          <div className="hint">
            How much the player character talks, and how. The single biggest
            lever on whether generated scenes feel like your VN.
          </div>
        </div>
      </div>

      <h3>Roleplay</h3>
      <p className="lede">
        A live conversation with the cast, kept in its own tables and never in
        the graph. The persona — who you are in a scene — is per project and
        lives in the Roleplay view.
      </p>
      <div className="grid2">
        <div className="field">
          <label>Length of one reply</label>
          <textarea rows={3} value={settings.rp_reply_length ?? ""}
            onChange={(e) => setSettings({ ...settings, rp_reply_length: e.target.value })} />
          <div className="hint">
            Deliberately a range, not a target. A fixed number here is how every
            reply ends up the same size, which is the thing this mode is most
            prone to.
          </div>
        </div>
        <div className="field">
          <label>Previous scene: lines carried over</label>
          <input type="number" min={0} max={30}
            value={settings.rp_prev_scene_tail ?? 6}
            onChange={(e) => setSettings({
              ...settings, rp_prev_scene_tail: Number(e.target.value) })} />
          <div className="hint">
            The end of the last scene is shown in the prompt, marked as finished,
            so speech style carries over. 0 turns it off.
          </div>
        </div>
        <NumSetting settings={settings} setSettings={setSettings}
          k="rp_context_budget" label="Context budget (tokens)" step={500} />
        <NumSetting settings={settings} setSettings={setSettings}
          k="rp_history_budget" label="Transcript budget (tokens)" step={250} />
        <div className="field">
          <label>
            <input type="checkbox" checked={!!settings.rp_tts}
              onChange={(e) => setSettings({ ...settings, rp_tts: e.target.checked })} />
            {" "}Speak cast lines as they stream
          </label>
          <div className="hint">
            Needs the voice model resident for the whole session — loading it per
            line costs about twelve seconds, which is not real-time. Check the fit
            below before turning this on.
          </div>
        </div>
        <div className="field">
          <label>
            <input type="checkbox" checked={!!settings.rp_coresident}
              onChange={(e) => setSettings({ ...settings, rp_coresident: e.target.checked })} />
            {" "}Keep both models loaded during a session
          </label>
          <div className="hint">
            Holds LM Studio and ComfyUI resident together instead of swapping.
            Turning this off means voice lines are generated after the fact
            rather than as they arrive.
          </div>
        </div>
        <NumSetting settings={settings} setSettings={setSettings}
          k="rp_tts_vram_mb" label="Voice model VRAM (MiB)" step={250} />
      </div>
      <VoiceFitPanel />
      </>
  );
}

/**
 * Does real-time voice actually fit?
 *
 * Read live off the cards rather than assumed, because the honest answer depends
 * on the context length the LLM happens to be loaded at — and because the
 * original plan for this feature assumed the voice model cost 2-3 GB when it
 * measures nearer 6.5 GB, which is the difference between "fits" and "does not".
 */
function VoiceFitPanel() {
  const store = useStore();
  const [fit, setFit] = useState<Awaited<ReturnType<typeof api.rpVram>> | null>(null);
  const [busy, setBusy] = useState(false);

  const check = async () => {
    if (!store.project) return;
    setBusy(true);
    try {
      setFit(await api.rpVram(store.project.id));
    } catch {
      setFit(null);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { void check(); }, [store.project?.id]);

  return (
    <div className="card" style={{ maxWidth: 620, marginTop: 12 }}>
      <div className="card-head">
        <span className="name">Real-time voice: does it fit?</span>
        <span style={{ marginLeft: "auto" }} />
        <button className="tiny" disabled={busy} onClick={check}>
          {busy ? "…" : "Re-check"}
        </button>
      </div>
      {!fit && <p className="hint">Not checked yet.</p>}
      {fit && !fit.known && <p className="hint warn">{fit.note}</p>}
      {fit && fit.known && (
        <>
          <p>
            <span className={`pill ${fit.fits ? "good" : "bad"}`}>
              {fit.fits ? "fits" : "does not fit"}
            </span>
            {" "}
            <span className="hint">
              needs {fit.needed_mb} MiB on one card
            </span>
          </p>
          <table className="data">
            <thead>
              <tr><th>GPU</th><th>total</th><th>used</th><th>free</th></tr>
            </thead>
            <tbody>
              {(fit.cards ?? []).map((c) => (
                <tr key={c.index}>
                  <td>{c.index}: {c.name}</td>
                  <td className="mono">{c.total_mb}</td>
                  <td className="mono">{c.used_mb}</td>
                  <td className="mono">{c.free_mb}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={`hint ${fit.fits ? "" : "warn"}`}>{fit.note}</div>
          <div className="hint">
            The voice model is one model, so it has to fit on a single card —
            ComfyUI does not shard it across both.
          </div>
        </>
      )}
    </div>
  );
}

function NumSetting({ settings, setSettings, k, label, step }: {
  settings: any; setSettings: (s: any) => void; k: string; label: string; step: number;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type="number" step={step} value={settings[k] ?? 0}
        onChange={(e) => setSettings({ ...settings, [k]: parseFloat(e.target.value) || 0 })} />
    </div>
  );
}

function TemplatesTab() {
  const store = useStore();
  const [keys, setKeys] = useState<string[]>([]);
  const [key, setKey] = useState<string>("system.prose_gen");
  const [detail, setDetail] = useState<any>(null);
  const [body, setBody] = useState("");
  const [diff, setDiff] = useState<string[] | null>(null);

  useEffect(() => {
    if (!store.project) return;
    api.listTemplates(store.project.id).then((out) => setKeys(out.keys));
  }, [store.project?.id]);

  useEffect(() => {
    if (!store.project) return;
    api.getTemplate(store.project.id, key).then((out) => {
      setDetail(out);
      setBody(out.active?.body ?? out.default?.body ?? "");
      setDiff(null);
    });
  }, [key, store.project?.id]);

  const save = async () => {
    const out = await api.saveTemplate(store.project!.id, key, body, detail.active?.params ?? {});
    setDiff(out.diff);
    setDetail(await api.getTemplate(store.project!.id, key));
    store.notify(`saved v${out.template.version}`);
  };

  return (
    <div className="split">
      <div className="left">
        <div className="list">
          {keys.map((k) => (
            <div key={k} className={`item ${key === k ? "active" : ""}`} onClick={() => setKey(k)}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{k}</span>
            </div>
          ))}
        </div>
        {detail?.versions?.length > 0 && (
          <>
            <div className="section-title">Versions</div>
            <div className="list">
              {detail.versions.map((version: any) => (
                <div key={version.id} className={`item ${version.active ? "active" : ""}`}
                  onClick={async () => {
                    await api.activateTemplate(store.project!.id, key, version.id);
                    const next = await api.getTemplate(store.project!.id, key);
                    setDetail(next);
                    setBody(next.active?.body ?? "");
                    store.notify(`activated v${version.version}`);
                  }}>
                  <span>v{version.version}</span>
                  {version.active && <span className="pill good">active</span>}
                  <span className="sub">{(version.created_at ?? "").slice(5, 16)}</span>
                </div>
              ))}
            </div>
            {detail.versions.length > 1 && (
              <button className="tiny" style={{ marginTop: 6, width: "100%" }}
                onClick={async () => {
                  const versions = detail.versions.map((v: any) => v.version).sort((a: number, b: number) => a - b);
                  const out = await api.diffTemplate(store.project!.id, key,
                    versions[versions.length - 2], versions[versions.length - 1]);
                  setDiff(out.diff);
                }}>Diff last two</button>
            )}
          </>
        )}
      </div>

      <div className="right">
        <div className="card">
          <div className="card-head">
            <span className="name" style={{ fontFamily: "var(--mono)" }}>{key}</span>
            {detail?.active && <span className="pill">v{detail.active.version}</span>}
            <span style={{ marginLeft: "auto" }} />
            <button title="Saves the prompt that shipped with the tool as a new version. Your edits stay in the version list."
              onClick={async () => {
              await api.resetTemplate(store.project!.id, key);
              const next = await api.getTemplate(store.project!.id, key);
              setDetail(next);
              setBody(next.active?.body ?? "");
              store.notify("reset to the shipped default");
            }}>Reset to default</button>
            <button className="primary"
              title="Never overwrites — each save adds a version you can diff against and roll back to."
              onClick={save}>Save as new version</button>
          </div>
          <textarea rows={22} value={body} onChange={(e) => setBody(e.target.value)}
            style={{ fontFamily: "var(--mono)", fontSize: 12 }} />
          <div className="hint">
            Jinja-style interpolation. Saving never overwrites — it makes a new
            version, so you can A/B a change without losing the one that worked.
          </div>
        </div>

        {diff && (
          <div className="card">
            <div className="card-head"><span className="name">Diff</span></div>
            <pre className="diff">
              {diff.map((line, index) => (
                <div key={index}
                  className={line.startsWith("+") ? "add" : line.startsWith("-") ? "del"
                    : line.startsWith("@") ? "hdr" : ""}>{line}</div>
              ))}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

function SamplingTab() {
  const store = useStore();
  const [key, setKey] = useState("system.prose_gen");
  const [detail, setDetail] = useState<any>(null);
  const [params, setParams] = useState<Record<string, any>>({});

  useEffect(() => {
    if (!store.project) return;
    api.getTemplate(store.project.id, key).then((out) => {
      setDetail(out);
      setParams(out.active?.params ?? out.default?.params ?? {});
    });
  }, [key, store.project?.id]);

  const set = (k: string, v: any) => setParams({ ...params, [k]: v });

  return (
    <div className="card" style={{ maxWidth: 620 }}>
      <div className="card-head">
        <span className="name">Sampling per pass</span>
        <select style={{ width: 200 }} value={key} onChange={(e) => setKey(e.target.value)}>
          <option value="system.prose_gen">prose pass</option>
          <option value="system.structuring">structuring pass</option>
          <option value="system.summarize">summarizer</option>
          <option value="rp.system">roleplay reply</option>
          <option value="rp.summarize">roleplay: end scene</option>
        </select>
        <span style={{ marginLeft: "auto" }} />
        <button className="primary" onClick={async () => {
          await api.saveTemplate(store.project!.id, key, detail.active?.body ?? "", params);
          store.notify("sampling saved");
        }}>Save</button>
      </div>

      <div className="grid2">
        <div className="field">
          <label>temperature <span>{params.temperature ?? 0}</span></label>
          <input type="range" min={0} max={1.5} step={0.01} value={params.temperature ?? 0}
            onChange={(e) => set("temperature", parseFloat(e.target.value))} />
        </div>
        <div className="field">
          <label>min_p <span>{params.min_p ?? 0}</span></label>
          <input type="range" min={0} max={0.3} step={0.01} value={params.min_p ?? 0}
            onChange={(e) => set("min_p", parseFloat(e.target.value))} />
        </div>
      </div>
      <div className="grid2">
        <div className="field">
          <label>repetition_penalty <span>{params.repetition_penalty ?? 1}</span></label>
          <input type="range" min={1} max={1.3} step={0.01} value={params.repetition_penalty ?? 1}
            onChange={(e) => set("repetition_penalty", parseFloat(e.target.value))} />
        </div>
        <div className="field">
          <label>max_tokens</label>
          <input type="number" step={100} value={params.max_tokens ?? 1200}
            onChange={(e) => set("max_tokens", parseInt(e.target.value, 10) || 0)} />
        </div>
      </div>
      <div className="grid2">
        <div className="field">
          <label>top_p</label>
          <input type="number" step={0.05} value={params.top_p ?? 1}
            onChange={(e) => set("top_p", parseFloat(e.target.value))} />
        </div>
        <div className="field">
          <label>top_k</label>
          <input type="number" step={1} value={params.top_k ?? 0}
            onChange={(e) => set("top_k", parseInt(e.target.value, 10) || 0)} />
        </div>
      </div>

      <div className="hint">
        Leave top_k at 0 and top_p at 1.0 — min_p does the work. Push
        repetition_penalty past ~1.08 and the model starts dropping character
        traits to avoid repeating itself, which reads as the character going
        flat. The structuring pass wants very different values from the prose
        pass, which is why they are configured separately.
      </div>

      <div className="section-title">DRY / XTC (if your backend supports them)</div>
      <div className="grid2">
        <div className="field">
          <label>dry_multiplier</label>
          <input type="number" step={0.1} value={params.dry_multiplier ?? 0}
            onChange={(e) => set("dry_multiplier", parseFloat(e.target.value))} />
        </div>
        <div className="field">
          <label>xtc_probability</label>
          <input type="number" step={0.05} value={params.xtc_probability ?? 0}
            onChange={(e) => set("xtc_probability", parseFloat(e.target.value))} />
        </div>
      </div>
    </div>
  );
}

function WorkflowsTab() {
  const store = useStore();
  const [data, setData] = useState<any>(null);
  const [name, setName] = useState("txt2img");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getWorkflows().then((out) => {
      setData(out);
      setText(JSON.stringify(out.workflows[name], null, 2));
    });
  }, []);

  useEffect(() => {
    if (data) setText(JSON.stringify(data.workflows[name], null, 2));
  }, [name, data]);

  if (!data) return null;

  return (
    <div className="split">
      <div className="left">
        <div className="list">
          {Object.keys(data.workflows).map((n) => (
            <div key={n} className={`item ${name === n ? "active" : ""}`} onClick={() => setName(n)}>
              <span style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{n}</span>
            </div>
          ))}
        </div>

        <div className="section-title">Custom node classes</div>
        {Object.entries(data.node_classes).map(([k, v]) => (
          <div key={k} className="field">
            <label>{k}</label>
            <input value={String(v)} onChange={(e) => setData({
              ...data, node_classes: { ...data.node_classes, [k]: e.target.value },
            })} />
          </div>
        ))}
        <button style={{ width: "100%" }} onClick={async () => {
          await api.putNodeClasses(data.node_classes);
          store.notify("node classes saved");
        }}>Save node classes</button>
        <div className="hint">
          These vary by install. Background removal, the WD14 tagger and the
          IndexTTS-2.5 wrapper all come from custom nodes. The tts_* names ending
          in _class name the TTS Audio Suite nodes the <code>tts</code> template
          uses; tts_model_loader_class / tts_class / tts_model_dir belong to the
          older <code>tts_indextts2</code> template.
        </div>
      </div>

      <div className="right">
        <div className="card">
          <div className="card-head">
            <span className="name" style={{ fontFamily: "var(--mono)" }}>{name}</span>
            <span style={{ marginLeft: "auto" }} />
            <button title="Restores the workflow JSON that shipped with the tool, discarding your edits to this one."
              onClick={async () => {
              await api.resetWorkflow(name);
              const out = await api.getWorkflows();
              setData(out);
              store.notify("reset to default");
            }}>Reset</button>
            <button className="primary" onClick={async () => {
              try {
                await api.putWorkflow(name, JSON.parse(text));
                setError(null);
                store.notify("workflow saved");
              } catch (e) {
                setError(String(e));
              }
            }}>Save</button>
          </div>
          <textarea rows={26} value={text} onChange={(e) => setText(e.target.value)}
            style={{ fontFamily: "var(--mono)", fontSize: 11.5 }} />
          {error && <div className="hint bad">{error}</div>}
          <div className="hint">
            ComfyUI API-format graph. <code>{"{{tokens}}"}</code> are injected per
            job: positive, negative, seed, width, height, steps, cfg, sampler,
            scheduler, checkpoint, lora_name, lora_weight, prefix.
          </div>
        </div>
      </div>
    </div>
  );
}

function EmotionTab() {
  const store = useStore();
  const [map, setMap] = useState<Record<string, any>>({});
  const dims = store.vocab?.emotion_dims ?? [];

  useEffect(() => { api.getEmotionMap().then((out) => setMap(out.map)); }, []);

  const setValue = (emotion: string, dim: string, value: number) => {
    const entry = map[emotion] ?? { vec: {} };
    const vec = { ...entry.vec };
    if (value <= 0) delete vec[dim]; else vec[dim] = value;
    setMap({ ...map, [emotion]: { ...entry, vec } });
  };

  return (
    <div className="card">
      <div className="card-head">
        <span className="name">Emotion → IndexTTS-2.5 vector</span>
        <span style={{ marginLeft: "auto" }} />
        <button className="primary" onClick={async () => {
          await api.putEmotionMap(map);
          store.notify("emotion map saved");
        }}>Save</button>
      </div>
      <div className="hint" style={{ marginBottom: 10 }}>
        8 sliders. TTS Audio Suite's <code>IndexTTS-2 Emotion Vectors</code> node
        caps each at 1.2 and the sum must stay ≤ 1.5 — anything over is scaled back
        automatically. These are base values; a line's <em>intensity</em> and the
        character's <code>emotion_scale</code> scale them down from here, because
        strong emotion causes slight timbre leakage.
      </div>
      <table className="data">
        <thead>
          <tr>
            <th>emotion</th>
            {dims.map((d) => <th key={d}>{d}</th>)}
            <th>speed</th>
            <th>sum</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(map).map(([emotion, entry]: [string, any]) => {
            const sum = Object.values(entry.vec ?? {}).reduce((a: number, b: any) => a + Number(b), 0);
            return (
              <tr key={emotion}>
                <td>{emotion}</td>
                {dims.map((dim) => (
                  <td key={dim}>
                    <input type="number" min={0} max={1.2} step={0.05} style={{ width: 62 }}
                      value={entry.vec?.[dim] ?? 0}
                      onChange={(e) => setValue(emotion, dim, parseFloat(e.target.value) || 0)} />
                  </td>
                ))}
                <td>
                  <input type="number" step={0.01} style={{ width: 62 }}
                    value={entry.speed ?? 1}
                    onChange={(e) => setMap({
                      ...map, [emotion]: { ...entry, speed: parseFloat(e.target.value) || 1 },
                    })} />
                </td>
                <td className={sum > 1.5 ? "" : ""}>
                  <span className={`pill ${sum > 1.5 ? "warn" : ""}`}>{sum.toFixed(2)}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


/** The booru tag dictionary: what autocomplete suggests from, and what the
 *  shot director's output gets checked against.
 *
 *  Fetched rather than shipped: it is ~30k rows, it changes, and this machine
 *  already has the network access. Everything works without it -- suggestions
 *  fall back to the curated CG vocabulary and the director's validator stays
 *  off -- so this is an upgrade, not a setup step. */
function TagDictionaryCard() {
  const store = useStore();
  const [status, setStatus] = useState<Awaited<
    ReturnType<typeof api.tagStatus>> | null>(null);
  const [minCount, setMinCount] = useState(100);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await api.tagStatus());
    } catch (e) {
      store.fail(String(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="card">
      <div className="card-head">
        <span className="name">Booru tag dictionary</span>
        {status && (
          <span className={`pill ${status.curated_only ? "" : "good"}`}>
            {status.curated_only
              ? `${status.total} curated only`
              : `${status.total} tags`}
          </span>
        )}
        <span className="spacer" />
        <button onClick={load}>Refresh</button>
      </div>

      <div className="hint" style={{ marginBottom: 8 }}>
        Autocomplete for every field whose text reaches ComfyUI verbatim — a
        CG's camera and action, a character's marks and accessories, an
        outfit's tags, a location's tags, every negative. A mistyped tag does
        not fail, it conditions on nothing, and the only way to notice is to
        study the image and wonder.
      </div>

      <div className="grid2" style={{ gap: 10 }}>
        <div>
          <div className="field">
            <label>Minimum post count</label>
            <input type="number" min={1} step={50} value={minCount}
              onChange={(e) => setMinCount(parseInt(e.target.value, 10) || 1)} />
            <div className="hint">
              Below roughly 100 posts a tag is in the checkpoint's training
              data as a handful of images, which it has not really learned —
              suggesting those makes the list longer and worse. Lower it only
              if you are chasing something specific.
            </div>
          </div>
          <button className="primary" disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await api.importTags(minCount);
                store.notify("importing tags — a few dozen requests");
                store.refreshJobs();
              } catch (e) {
                store.fail(String(e));
              } finally {
                setBusy(false);
              }
            }}>
            {status?.curated_only ? "Import from Danbooru" : "Re-import"}
          </button>
          <div className="hint" style={{ marginTop: 4 }}>
            Ordered by post count and stopped at the floor above, so it fetches
            the popular tail and no further. Never touches the GPU.
          </div>
        </div>

        <div>
          <div className="section-title">By category</div>
          {status && (
            <table className="data">
              <tbody>
                {Object.entries(status.categories)
                  .filter(([, n]) => n > 0)
                  .sort((a, b) => b[1] - a[1])
                  .map(([cat, n]) => (
                    <tr key={cat}><th>{cat}</th><td>{n}</td></tr>
                  ))}
              </tbody>
            </table>
          )}
          {status?.imported_at && (
            <div className="hint" style={{ marginTop: 6 }}>
              Imported {status.imported_at.slice(0, 16)} at a floor of{" "}
              {status.min_count} posts.
            </div>
          )}
          {status?.curated_only && (
            <div className="hint warn" style={{ marginTop: 6 }}>
              No dictionary imported yet — suggestions come from the curated CG
              vocabulary, and the shot director's output is not spell-checked
              against real tags.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
