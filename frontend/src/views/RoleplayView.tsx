/**
 * Roleplay: a live conversation with the cast.
 *
 * Three panes -- who you can talk to, their history, and the open scene. None of
 * it touches the graph: a conversation is rows in `rp_conversations` /
 * `rp_messages`, and the only way into the canvas is the explicit port.
 *
 * The view owns almost all of its own state, because views unmount on every tab
 * switch -- which is why the two selections that must survive navigation
 * (character, open conversation) go through localStorage rather than useState
 * alone.
 *
 * Streaming follows the house pattern: `store.streams[jobId]` carries the reply
 * as it is written, a remount re-adopts an in-flight job so coming back to the
 * tab picks the stream up mid-reply, and a slow poll sits behind the SSE stream
 * so a dropped connection cannot leave the composer locked forever.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ConfirmDialog from "../components/ConfirmDialog";
import { api } from "../lib/api";
import { parseLiveReply, type LiveBeat } from "../lib/liveReply";
import { useStore } from "../lib/store";
import { useIsNarrow } from "../lib/useMediaQuery";
import type {
  RPCharacter, RPConversation, RPMessage, RPPersona, RPPreview, RPThread,
} from "../lib/types";

const ACTIVE = ["queued", "claimed", "running"];

const CHAR_KEY = (pid: string) => `vn:rpCharacter:${pid}`;
const CONV_KEY = (pid: string) => `vn:rpConversation:${pid}`;

function remember(key: string, value: string | null) {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* private window, blocked storage -- the selection just does not persist */
  }
}

function recall(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

// --- pure helpers, exported for the unit tests ---------------------------

/**
 * The feeling a performance bracket names, for the chip on a line.
 *
 * The bracket is written by the model in a known order (face, feeling, number,
 * note) but not reliably, so this reads it the way the backend does: by what
 * each token *is*. The chip shows the feeling rather than the face because the
 * feeling is what the voice does, and the voice is what the reader is hearing.
 */
export function emotionOf(tags: string | undefined): string {
  const parts = (tags ?? "").split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
  // The second slot is the feeling by convention. Falling back to the first
  // covers a line tagged with only one word, which is usually a feeling that
  // happens to also be a face ("crying", "surprised").
  return parts[1] ?? parts[0] ?? "";
}

/** How far from composure, if the bracket says. Drives the chip's weight. */
export function intensityOf(tags: string | undefined): number | null {
  for (const raw of (tags ?? "").split(",")) {
    const value = Number(raw.trim());
    if (raw.trim() !== "" && !Number.isNaN(value) && value >= 0 && value <= 1) return value;
  }
  return null;
}

/** Which take is showing, 1-based, and how many there are. */
export function takeAxis(message: RPMessage): { index: number; total: number } {
  const total = message.takes.length;
  const at = message.takes.findIndex((t) => t.id === message.active_take);
  return { index: at < 0 ? total : at + 1, total };
}

export type PlayerPart = { kind: "speech" | "action" | "steer"; text: string };

/**
 * A typed player message split for display.
 *
 * Kept in the UI rather than taken from the server because the raw text is what
 * is stored -- an edit has to show what was written, asterisks and brackets
 * included -- and the server's own split is about building a prompt, not about
 * rendering one.
 */
export function splitPlayerParts(text: string): PlayerPart[] {
  const out: PlayerPart[] = [];
  const pattern = /\*([^*\n]+)\*|\[([^\]\n]*)\]/g;
  let at = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text))) {
    const before = text.slice(at, match.index).trim();
    if (before) out.push({ kind: "speech", text: before });
    if (match[1] !== undefined) out.push({ kind: "action", text: match[1].trim() });
    else out.push({ kind: "steer", text: (match[2] ?? "").trim() });
    at = match.index + match[0].length;
  }
  const rest = text.slice(at).trim();
  if (rest) out.push({ kind: "speech", text: rest });
  return out.filter((p) => p.text);
}

// --- the view -------------------------------------------------------------

export default function RoleplayView() {
  const store = useStore();
  const narrow = useIsNarrow();
  const pid = store.project?.id ?? "";

  const [characters, setCharacters] = useState<RPCharacter[]>([]);
  // The persona as a prompt would use it -- fallbacks already applied. The
  // editor fetches the stored version separately, so its fields can show what
  // was actually typed and offer the fallback as placeholder text.
  const [persona, setPersona] = useState<RPPersona | null>(null);
  const [characterId, setCharacterId] = useState<string | null>(
    () => (pid ? recall(CHAR_KEY(pid)) : null));
  const [conversations, setConversations] = useState<RPConversation[]>([]);
  const [convId, setConvId] = useState<string | null>(
    () => (pid ? recall(CONV_KEY(pid)) : null));
  const [thread, setThread] = useState<RPThread | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState("");
  const [starting, setStarting] = useState(false);
  const [ideas, setIdeas] = useState<string[]>([]);
  const [preview, setPreview] = useState<RPPreview | null>(null);
  const [personaOpen, setPersonaOpen] = useState(false);
  const [confirm, setConfirm] =
    useState<null | { kind: "conv" | "message" | "report"; id: string }>(null);
  const [pane, setPane] = useState<"cast" | "history" | "chat">("cast");
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  const job = jobId ? store.jobs.find((j) => j.id === jobId) : undefined;
  const stream = jobId ? store.streams[jobId] : undefined;
  const running = !!job && ACTIVE.includes(job.status);
  const logRef = useRef<HTMLDivElement>(null);

  // --- loading ----------------------------------------------------------

  const loadCast = useCallback(async () => {
    if (!pid) return;
    const out = await api.rpCharacters(pid);
    setCharacters(out.characters);
    setPersona(out.persona);
    setCharacterId((current) => current ?? out.characters[0]?.id ?? null);
  }, [pid]);

  const loadHistory = useCallback(async (cid: string | null) => {
    if (!pid || !cid) {
      setConversations([]);
      return;
    }
    const out = await api.rpConversations(pid, cid);
    setConversations(out.conversations);
  }, [pid]);

  const loadThread = useCallback(async (id: string | null) => {
    if (!pid || !id) {
      setThread(null);
      return;
    }
    try {
      setThread(await api.rpThread(pid, id));
    } catch {
      // Deleted from under us, or belongs to another project.
      setThread(null);
      setConvId(null);
      remember(CONV_KEY(pid), null);
    }
  }, [pid]);

  useEffect(() => { void loadCast(); }, [loadCast]);
  useEffect(() => {
    remember(CHAR_KEY(pid), characterId);
    void loadHistory(characterId);
  }, [characterId, loadHistory, pid]);
  useEffect(() => {
    remember(CONV_KEY(pid), convId);
    void loadThread(convId);
  }, [convId, loadThread, pid]);

  // A reply already in flight when the tab was left: adopt it so coming back
  // shows the rest of it arriving rather than a frozen composer.
  useEffect(() => {
    if (jobId || !convId) return;
    const live = store.jobs.find(
      (j) => j.kind === "rp.reply" && ACTIVE.includes(j.status)
        && j.payload?.conv_id === convId);
    if (live) setJobId(live.id);
  }, [store.jobs, convId, jobId]);

  // The safety net under SSE. Without it a dropped event stream leaves the job
  // looking permanently active and the composer permanently disabled.
  useEffect(() => {
    if (!jobId || !running) return;
    const timer = setInterval(() => {
      void api.getJob(jobId).then(store.upsertJob).catch(() => undefined);
    }, 2500);
    return () => clearInterval(timer);
  }, [jobId, running]);

  // Reload before dropping the live text, so the reply never blinks out in the
  // gap between the stream ending and the row arriving.
  useEffect(() => {
    if (!job || ACTIVE.includes(job.status)) return;
    (async () => {
      await loadThread(convId);
      await loadHistory(characterId);
      if (job.status === "error") store.fail(job.error || "the reply failed");
      setJobId(null);
    })();
  }, [job?.status]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread?.messages.length, stream?.text, convId]);

  const character = characters.find((c) => c.id === characterId) ?? null;

  // --- actions ----------------------------------------------------------

  /** One wrapper for every job-starting action, per the house pattern. */
  const run = async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    try {
      return await fn();
    } catch (err) {
      store.fail(String(err instanceof Error ? err.message : err));
      return undefined;
    } finally {
      setBusy(false);
      void store.refreshJobs();
    }
  };

  const send = async () => {
    if (!convId || running) return;
    const text = draft;
    setDraft("");
    const out = await run(() => api.rpTurn(pid, convId, text));
    if (!out) {
      setDraft(text);          // put it back rather than losing what they typed
      return;
    }
    setJobId(out.job_id);
    await loadThread(convId);
  };

  const startConversation = async (context: string) => {
    if (!characterId) return;
    const conv = await run(() => api.rpCreateConversation(pid, {
      character_id: characterId, scene_context: context.trim(),
    }));
    if (!conv) return;
    setStarting(false);
    setIdeas([]);
    await loadHistory(characterId);
    setConvId(conv.id);
    if (narrow) setPane("chat");
    // An opening with no player message: the cast start the scene.
    const out = await run(() => api.rpTurn(pid, conv.id, ""));
    if (out) setJobId(out.job_id);
  };

  const suggest = async () => {
    if (!characterId) return;
    const out = await run(() => api.rpSeed(pid, characterId));
    if (!out) return;
    // Polled rather than pushed: the seed job is short and its result is not
    // streamed, so there is nothing to subscribe to.
    const poll = setInterval(async () => {
      const seed = await api.getJob(out.job_id).catch(() => null);
      if (!seed || ACTIVE.includes(seed.status)) return;
      clearInterval(poll);
      if (seed.status === "done") setIdeas((seed.result?.ideas as string[]) ?? []);
      else store.fail(seed.error || "could not think of anything");
    }, 1200);
  };

  const swipe = async (message: RPMessage, delta: number) => {
    const { index, total } = takeAxis(message);
    const next = index + delta;
    if (next < 1) return;
    if (next > total) {
      // Past the last take is a request for a new one.
      const out = await run(() => api.rpRegenerate(pid, convId!, message.id));
      if (out) setJobId(out.job_id);
      return;
    }
    const take = message.takes[next - 1];
    await run(() => api.rpPatchMessage(pid, message.id, { active_take: take.id }));
    await loadThread(convId);
  };

  const saveEdit = async (message: RPMessage) => {
    await run(() => api.rpPatchMessage(pid, message.id, { text: editText }));
    setEditing(null);
    await loadThread(convId);
  };

  const endScene = async () => {
    if (!convId) return;
    const out = await run(() => api.rpEnd(pid, convId, thread?.state === "ended"));
    if (!out) return;
    const poll = setInterval(async () => {
      const done = await api.getJob(out.job_id).catch(() => null);
      if (!done || ACTIVE.includes(done.status)) return;
      clearInterval(poll);
      if (done.status === "error") store.fail(done.error || "could not summarize");
      await loadThread(convId);
      await loadHistory(characterId);
    }, 1500);
  };

  /** Copy this conversation into the canvas as a new scene, and go there.
   *
   *  The one action in this view that writes to the graph, and it only ever
   *  adds: a second port builds a second scene rather than merging into the
   *  first, which is why it asks before doing it again. */
  const portScene = async () => {
    if (!convId) return;
    const out = await run(() => api.rpPort(pid, convId));
    if (!out) return;
    const poll = setInterval(async () => {
      const done = await api.getJob(out.job_id).catch(() => null);
      if (!done || ACTIVE.includes(done.status)) return;
      clearInterval(poll);
      if (done.status === "error") {
        store.fail(done.error || "could not port this scene");
        return;
      }
      await loadThread(convId);
      await loadHistory(characterId);
      await store.refreshGraph();
      const sceneId = done.result?.scene_id as string | undefined;
      // Straight to it. A port that leaves you looking at the conversation you
      // just ported gives no sign anything happened.
      if (sceneId) store.focusNode(sceneId, "canvas");
    }, 1500);
  };

  const beats: LiveBeat[] = useMemo(
    () => (stream && !stream.done ? parseLiveReply(stream.text) : []), [stream]);

  if (!pid) return <div className="view"><p className="hint">Open a project first.</p></div>;

  // --- panes ------------------------------------------------------------

  const castPane = (
    <div className="rp-cast">
      <div className="rp-pane-head">
        <strong>Cast</strong>
        <span className="spacer" />
        <button className="tiny ghost" onClick={() => setPersonaOpen(true)}
          title="Who you are in a scene">
          {persona?.name || "Persona"} ✎
        </button>
      </div>
      <div className="rp-cast-list">
        {characters.length === 0 && (
          <p className="hint">
            No cast yet. Add a character on the Characters tab — a name and a
            SPEECH line are enough to talk to them.
          </p>
        )}
        {characters.map((c) => (
          <button key={c.id}
            className={`rp-cast-card ${c.id === characterId ? "on" : ""}`}
            onClick={() => {
              setCharacterId(c.id);
              setConvId(null);
              if (narrow) setPane("history");
            }}>
            <Avatar hash={c.profile_image} name={c.name} color={c.color} />
            <span className="rp-cast-body">
              <span className="rp-cast-name">{c.name}</span>
              {/* One line under the portrait. "no scenes yet · voiced" wrapped
                  to two under every tile and made the grid ragged, so the
                  voice is a mark rather than a word. */}
              <span className="hint">
                {c.conversations === 0 ? "no scenes"
                  : `${c.conversations} scene${c.conversations === 1 ? "" : "s"}`}
                {c.voiced && <span className="rp-voiced" title="Has a voice">♪</span>}
              </span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );

  const historyPane = (
    <div className="rp-history">
      <div className="rp-pane-head">
        <strong>{character ? character.name : "History"}</strong>
        <span className="spacer" />
        <button className="primary tiny" disabled={!characterId || busy}
          onClick={() => { setStarting(true); setIdeas([]); }}>
          New scene
        </button>
      </div>

      {starting && (
        <NewScene
          characterName={character?.name ?? ""}
          ideas={ideas}
          busy={busy}
          onSuggest={suggest}
          onCancel={() => { setStarting(false); setIdeas([]); }}
          onStart={startConversation}
        />
      )}

      <div className="rp-history-list">
        {conversations.length === 0 && !starting && (
          <p className="hint">
            Nothing yet. A scene can start from a line of context — “{
              character?.name ?? "she"} has just got home and you are already
            there” — or from nothing at all.
          </p>
        )}
        {conversations.map((conv, index) => (
          <div key={conv.id}
            className={`rp-scene ${conv.id === convId ? "on" : ""} ${conv.hidden ? "hid" : ""}`}
            onClick={() => { setConvId(conv.id); if (narrow) setPane("chat"); }}>
            <div className="rp-scene-head">
              <span className="rp-scene-n">{index + 1}</span>
              <span className={`rp-scene-title ${conv.untitled ? "derived" : ""}`}>
                {conv.title}
              </span>
              {conv.state === "ended" && <span className="pill good">ended</span>}
              {conv.scene_id && <span className="pill" title="Already a scene on the canvas">ported</span>}
              {conv.hidden && <span className="pill">hidden</span>}
            </div>
            <div className="rp-scene-meta hint">
              {(conv.created_at ?? "").slice(0, 10)}
              {conv.message_count ? ` · ${conv.message_count} messages` : ""}
              {conv.cast.length ? ` · with ${conv.cast.length + 1}` : ""}
            </div>
            <span className="row-actions">
              <button className="tiny ghost" title={conv.hidden
                ? "Remember this scene again"
                : "Keep it readable but take it out of what later scenes remember"}
                onClick={(e) => {
                  e.stopPropagation();
                  void run(() => api.rpPatchConversation(pid, conv.id,
                    { hidden: !conv.hidden })).then(() => loadHistory(characterId));
                }}>
                {conv.hidden ? "unhide" : "hide"}
              </button>
              <button className="tiny ghost danger" title="Delete this scene"
                onClick={(e) => { e.stopPropagation(); setConfirm({ kind: "conv", id: conv.id }); }}>
                ✕
              </button>
            </span>
          </div>
        ))}
      </div>
    </div>
  );

  const chatPane = (
    <div className="rp-chat">
      {!thread && (
        <div className="rp-empty">
          <p className="hint">
            {character
              ? `Pick a scene on the left, or start a new one with ${character.name}.`
              : "Pick somebody to talk to."}
          </p>
        </div>
      )}

      {thread && (
        <>
          <div className="rp-chat-head">
            <strong className="rp-chat-title">{thread.title}</strong>
            {thread.state === "ended" && <span className="pill good">ended</span>}
            <span className="spacer" />
            {/* Four labelled buttons do not fit beside a title on a phone: they
                wrapped to two lines each and pushed the header to 56px. Narrow
                gets the same actions behind one ⋯ instead. */}
            <SceneActions
              compact={narrow}
              thread={thread}
              disabled={busy || running}
              onPrompt={() => { void api.rpPreview(pid, thread.id).then(setPreview); }}
              onEnd={endScene}
              onPort={() => (thread.scene_id
                ? setConfirm({ kind: "report", id: thread.id })
                : void portScene())}
              onGoToScene={() => store.focusNode(thread.scene_id!, "canvas")}
            />
          </div>

          {/* The opening context, folded away.
              It is the setup you wrote once and then stopped needing: pinned
              open it sat above every reply for the rest of the scene, and on a
              phone a two-sentence setup is three lines of the conversation
              gone. Open while the scene is still empty, because then it is the
              only thing there is to read; a tap away after that.
              Not shown at all when the title is the same sentence -- an
              untitled scene is titled from its context, so it would be printing
              it twice. */}
          {thread.scene_context
            && thread.scene_context.trim() !== thread.title.trim() && (
            <details className="rp-context" open={!thread.messages.length}>
              <summary>Scene context</summary>
              <p className="hint">{thread.scene_context}</p>
            </details>
          )}

          <div className="rp-log" ref={logRef}>
            {thread.messages.map((message) => (
              <Message
                key={message.id}
                message={message}
                character={characters.find(
                  (c) => c.id === thread.character_id) ?? null}
                personaName={persona?.name || "You"}
                editing={editing === message.id}
                editText={editText}
                setEditText={setEditText}
                onEdit={() => { setEditing(message.id); setEditText(message.text); }}
                onCancelEdit={() => setEditing(null)}
                onSaveEdit={() => void saveEdit(message)}
                onSwipe={(delta) => void swipe(message, delta)}
                onRegenerate={() => void run(() =>
                  api.rpRegenerate(pid, thread.id, message.id))
                  .then((out) => out && setJobId(out.job_id))}
                onRewind={() => setConfirm({ kind: "message", id: message.id })}
                disabled={busy || running}
              />
            ))}

            {running && (
              <div className="rp-msg cast live">
                <Avatar hash={character?.profile_image ?? null}
                  name={character?.name ?? "?"} color={character?.color ?? "#888"} />
                <div className="rp-body">
                  {beats.length === 0 && (
                    <span className="rp-typing">
                      <i /><i /><i />
                    </span>
                  )}
                  {beats.map((beat, i) => (
                    <Beat key={i} beat={beat} showName={multiSpeaker(beats)} />
                  ))}
                </div>
              </div>
            )}

            {thread.state === "ended" && thread.summary && (
              <Summary
                text={thread.summary}
                edited={thread.summary_edited}
                onSave={(text) => void run(() =>
                  api.rpPatchConversation(pid, thread.id, { summary: text }))
                  .then(() => loadThread(thread.id))}
              />
            )}
          </div>

          {/* One row: the box and the button that sends it. The notation hint
              that used to sit under here is on the box itself as a tooltip --
              it was a line of small print under every scene, and on a phone it
              took a seventh of the screen to say something you need once. */}
          <div className="rp-composer">
            <textarea
              rows={2}
              value={draft}
              placeholder={`Say something as ${persona?.name || "you"}…`}
              title={"*like this* is something you do · [like this] steers the "
                     + "scene and is said by nobody · send an empty box to let "
                     + "it carry on without you"}
              disabled={running}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            {running ? (
              <button className="rp-send danger" aria-label="Stop"
                title="Stop the reply" onClick={() => {
                  if (jobId) void api.cancelJob(jobId).catch(() => undefined);
                }}>■</button>
            ) : (
              /* An empty box still sends -- it means "carry on without me" --
                 so the button is never disabled for being empty, and the label
                 is what says which of the two you are about to do. */
              <button className="rp-send primary" disabled={busy} onClick={send}
                aria-label={draft.trim() ? "Send" : "Carry on without me"}
                title={draft.trim() ? "Send" : "Carry on without me"}>➤</button>
            )}
          </div>
        </>
      )}
    </div>
  );

  return (
    <div className="view rp-view">
      {narrow && (
        <div className="rp-tabs">
          {(["cast", "history", "chat"] as const).map((id) => (
            <button key={id} className={pane === id ? "active" : ""}
              onClick={() => setPane(id)}>
              {id === "cast" ? "Cast" : id === "history" ? "Scenes" : "Scene"}
            </button>
          ))}
        </div>
      )}
      {(!narrow || pane === "cast") && castPane}
      {(!narrow || pane === "history") && historyPane}
      {(!narrow || pane === "chat") && chatPane}

      {preview && <PromptPreview preview={preview} onClose={() => setPreview(null)} />}
      {personaOpen && (
        <PersonaEditor
          projectId={pid}
          onClose={() => setPersonaOpen(false)}
          onSave={async (next) => {
            const out = await run(() => api.rpSavePersona(pid, next));
            if (out) {
              setPersona(out.resolved);
              setPersonaOpen(false);
            }
          }}
        />
      )}
      {confirm?.kind === "conv" && (
        <ConfirmDialog title="Delete this scene?"
          onCancel={() => setConfirm(null)}
          onConfirm={async () => {
            await run(() => api.rpDeleteConversation(pid, confirm.id));
            if (confirm.id === convId) setConvId(null);
            setConfirm(null);
            await loadHistory(characterId);
            await loadCast();
          }}>
          <p>Every message in it goes too, and later scenes stop remembering it.
            This cannot be undone.</p>
        </ConfirmDialog>
      )}
      {confirm?.kind === "report" && (
        <ConfirmDialog title="Port this scene again?" confirmLabel="Port again"
          onCancel={() => setConfirm(null)}
          onConfirm={async () => {
            setConfirm(null);
            await portScene();
          }}>
          <p>This conversation is already a scene on the canvas. Porting builds a
            second one beside it rather than updating the first -- the copy is
            one way, so nothing you have edited over there is touched.</p>
        </ConfirmDialog>
      )}
      {confirm?.kind === "message" && (
        <ConfirmDialog title="Rewind to here?" confirmLabel="Rewind"
          onCancel={() => setConfirm(null)}
          onConfirm={async () => {
            await run(() => api.rpDeleteMessage(pid, confirm.id, true));
            setConfirm(null);
            await loadThread(convId);
          }}>
          <p>This message and everything after it is deleted, so the scene
            carries on from just before it.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

// --- pieces ---------------------------------------------------------------

function Avatar({ hash, name, color }: {
  hash: string | null; name: string; color: string;
}) {
  if (hash) {
    return <img className="rp-avatar" src={api.assetUrl(hash)} alt={name} />;
  }
  return (
    <span className="rp-avatar fallback" style={{ background: color }}>
      {name.trim().slice(0, 1).toUpperCase() || "?"}
    </span>
  );
}

/**
 * One beat of a reply -- a spoken line with its chip, or narration.
 *
 * `showName` is on only when the reply contains more than one speaker. With a
 * single character the name is already on the message and repeating it per line
 * is noise; in a scene with two of them it is the only thing saying who is
 * talking, since the performance bracket is hidden.
 */
function Beat({ beat, showName }: { beat: LiveBeat; showName?: boolean }) {
  if (beat.kind === "narration") {
    return <p className="rp-narr">{beat.text}</p>;
  }
  const emotion = emotionOf(beat.tags);
  const intensity = intensityOf(beat.tags);
  return (
    <p className="rp-say">
      {showName && beat.name && <span className="rp-speaker">{beat.name}</span>}
      <span className="rp-line">{beat.text}</span>
      {emotion && (
        <span className={`rp-chip ${intensity !== null && intensity >= 0.7 ? "hot" : ""}`}>
          {emotion}
        </span>
      )}
    </p>
  );
}

/** True when a reply has more than one person speaking in it. */
export function multiSpeaker(beats: LiveBeat[]): boolean {
  const names = new Set(
    beats.filter((b) => b.kind === "say" && b.name).map((b) => b.name!.toLowerCase()));
  return names.size > 1;
}

function Message({
  message, character, personaName, editing, editText, setEditText,
  onEdit, onCancelEdit, onSaveEdit, onSwipe, onRegenerate, onRewind, disabled,
}: {
  message: RPMessage;
  character: RPCharacter | null;
  personaName: string;
  editing: boolean;
  editText: string;
  setEditText: (v: string) => void;
  onEdit: () => void;
  onCancelEdit: () => void;
  onSaveEdit: () => void;
  onSwipe: (delta: number) => void;
  onRegenerate: () => void;
  onRewind: () => void;
  disabled: boolean;
}) {
  const mine = message.role === "player";
  const { index, total } = takeAxis(message);

  return (
    <div className={`rp-msg ${mine ? "me" : "cast"}`}>
      {!mine && (
        <Avatar hash={character?.profile_image ?? null}
          name={character?.name ?? "?"} color={character?.color ?? "#888"} />
      )}
      <div className="rp-body">
        <div className="rp-who hint">{mine ? personaName : character?.name ?? "the scene"}</div>

        {editing ? (
          <div className="rp-edit">
            <textarea rows={Math.min(14, editText.split("\n").length + 2)}
              value={editText} onChange={(e) => setEditText(e.target.value)} autoFocus />
            <div className="rp-edit-actions">
              <button onClick={onCancelEdit}>Cancel</button>
              <button className="primary" onClick={onSaveEdit}>Save</button>
            </div>
          </div>
        ) : mine ? (
          <p className="rp-player-text">
            {splitPlayerParts(message.text).map((part, i) => (
              <span key={i} className={`rp-part ${part.kind}`}>
                {part.kind === "action" ? `*${part.text}*`
                  : part.kind === "steer" ? `[${part.text}]` : part.text}
              </span>
            ))}
          </p>
        ) : (
          (() => {
            const beats = parseLiveReply(message.text, true);
            const many = multiSpeaker(beats);
            return beats.map((beat, i) => <Beat key={i} beat={beat} showName={many} />);
          })()
        )}

        {!editing && (
          <span className="row-actions">
            {!mine && total > 1 && (
              <>
                <button className="tiny ghost" disabled={disabled || index <= 1}
                  onClick={() => onSwipe(-1)} title="Previous take">◀</button>
                <span className="hint">{index}/{total}</span>
              </>
            )}
            {!mine && (
              <button className="tiny ghost" disabled={disabled} onClick={() => onSwipe(1)}
                title={index >= total ? "Write another take" : "Next take"}>▶</button>
            )}
            <PopMenu label="⋯" ariaLabel="More">
              {(close) => (
                <>
                  <button onClick={() => { close(); onEdit(); }}>Edit</button>
                  {!mine && (
                    <button disabled={disabled}
                      onClick={() => { close(); onRegenerate(); }}>
                      Regenerate
                    </button>
                  )}
                  <button onClick={() => {
                    close();
                    void navigator.clipboard.writeText(message.text).catch(() => undefined);
                  }}>Copy</button>
                  <div className="menu-sep" />
                  <button onClick={() => { close(); onRewind(); }}>
                    Rewind to here
                  </button>
                </>
              )}
            </PopMenu>
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * The scene's summary, below every message and deliberately not one of them.
 *
 * It is memory for later scenes, not something anybody said, so it sits outside
 * the log's rhythm and says what it is.
 */
/**
 * A `⋯` menu that is not clipped by whatever it is sitting inside.
 *
 * The app's `.insert-menu` popover is an absolutely-positioned child of its
 * trigger, which works fine in a toolbar and not at all in the message log:
 * `.rp-log` scrolls, so it clips its own descendants, and a menu on the last
 * message was cut off at the log's bottom edge and looked like it was behind
 * the composer. `z-index` cannot help -- clipping by an ancestor's overflow is
 * not a stacking question.
 *
 * So the menu is `position: fixed` and placed from the trigger's own rect,
 * flipping above it when there is no room below and clamping to the viewport on
 * both sides. That also covers the case the message log never will: a trigger
 * near the right edge of a phone.
 */
function PopMenu({ label, ariaLabel, children, align = "right" }: {
  label: string;
  ariaLabel?: string;
  align?: "left" | "right";
  children: (close: () => void) => React.ReactNode;
}) {
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  // Measured after the menu exists, because where it goes depends on how tall
  // it turned out to be. One layout pass with it at 0,0 is invisible.
  useEffect(() => {
    if (!at || !trigger.current || !menu.current) return;
    const t = trigger.current.getBoundingClientRect();
    const m = menu.current.getBoundingClientRect();
    const margin = 8;
    const below = t.bottom + 4;
    const top = below + m.height > window.innerHeight - margin
      ? Math.max(margin, t.top - m.height - 4)     // no room under it: flip up
      : below;
    const wanted = align === "right" ? t.right - m.width : t.left;
    const left = Math.min(Math.max(margin, wanted), window.innerWidth - m.width - margin);
    if (Math.abs(top - at.top) > 0.5 || Math.abs(left - at.left) > 0.5) setAt({ top, left });
  }, [at, align]);

  const close = () => setAt(null);
  return (
    <span className="rp-pop">
      <button ref={trigger} className="tiny ghost" aria-label={ariaLabel}
        onClick={() => setAt(at ? null : { top: 0, left: 0 })}>{label}</button>
      {at && (
        <>
          <div className="menu-scrim" onClick={close} />
          <div ref={menu} className="menu rp-pop-menu"
            style={{ top: at.top, left: at.left }}>
            {children(close)}
          </div>
        </>
      )}
    </span>
  );
}

/**
 * What you can do to the open scene: the same four actions either way.
 *
 * `compact` folds them behind one `⋯`. Four labelled buttons plus a title is
 * about 340px of text, which a phone does not have -- laid out flat they
 * wrapped every label onto two lines and doubled the height of the header.
 * The popover is the app's existing `.insert-menu` idiom, whose `.menu` rules
 * are scoped under that class, so the wrapper has to keep it.
 */
function SceneActions({ compact, thread, disabled, onPrompt, onEnd, onPort, onGoToScene }: {
  compact: boolean;
  thread: RPThread;
  disabled: boolean;
  onPrompt: () => void;
  onEnd: () => void;
  onPort: () => void;
  onGoToScene: () => void;
}) {
  const empty = !thread.messages.length;
  const items: [string, string, boolean, () => void][] = [
    ["prompt", "Prompt", disabled, onPrompt],
    ["end", thread.state === "ended" ? "Re-summarize" : "End scene", disabled || empty, onEnd],
    ["port", thread.scene_id ? "Port again" : "Port to scene", disabled || empty, onPort],
  ];
  if (thread.scene_id) items.push(["goto", "Go to scene →", false, onGoToScene]);

  if (!compact) {
    return (
      <span className="rp-chat-actions">
        {items.map(([key, label, off, run]) => (
          <button key={key} className="tiny ghost" disabled={off} onClick={run}>{label}</button>
        ))}
      </span>
    );
  }
  return (
    <PopMenu label="⋯" ariaLabel="Scene actions">
      {(close) => items.map(([key, label, off, run]) => (
        <button key={key} disabled={off}
          onClick={() => { close(); run(); }}>{label}</button>
      ))}
    </PopMenu>
  );
}

function Summary({ text, edited, onSave }: {
  text: string; edited: boolean; onSave: (text: string) => void;
}) {
  const [draft, setDraft] = useState(text);
  const [open, setOpen] = useState(false);
  useEffect(() => setDraft(text), [text]);

  return (
    <div className="rp-summary">
      <div className="rp-summary-head">
        <strong>What later scenes will remember</strong>
        {edited && <span className="pill">edited</span>}
        <span className="spacer" />
        <button className="tiny ghost" onClick={() => setOpen(!open)}>
          {open ? "Done" : "Edit"}
        </button>
      </div>
      <p className="hint">
        Not part of the conversation. This is what gets read back into the
        prompt for every scene after this one.
      </p>
      {open ? (
        <>
          <textarea rows={10} value={draft} onChange={(e) => setDraft(e.target.value)} />
          <div className="rp-edit-actions">
            <button className="primary" onClick={() => { onSave(draft); setOpen(false); }}>
              Save
            </button>
          </div>
        </>
      ) : (
        <pre className="rp-summary-body">{text}</pre>
      )}
    </div>
  );
}

function NewScene({ characterName, ideas, busy, onSuggest, onCancel, onStart }: {
  characterName: string;
  ideas: string[];
  busy: boolean;
  onSuggest: () => void;
  onCancel: () => void;
  onStart: (context: string) => void;
}) {
  const [context, setContext] = useState("");
  return (
    <div className="rp-new card">
      <div className="field">
        <label>How does this scene start?</label>
        <textarea rows={3} autoFocus value={context}
          placeholder={`e.g. you have come over to ${characterName || "her"}'s flat for dinner`}
          onChange={(e) => setContext(e.target.value)} />
        <div className="hint">
          Optional. Left empty, the cast open the scene from whatever happened
          last — or ask for an idea.
        </div>
      </div>
      {ideas.length > 0 && (
        <div className="rp-ideas">
          {ideas.map((idea, i) => (
            <button key={i} className="tiny" onClick={() => setContext(idea)}>{idea}</button>
          ))}
        </div>
      )}
      <div className="rp-edit-actions">
        <button onClick={onCancel}>Cancel</button>
        <button disabled={busy} onClick={onSuggest}>Suggest…</button>
        <button className="primary" disabled={busy} onClick={() => onStart(context)}>
          Start
        </button>
      </div>
    </div>
  );
}

/**
 * Who you are in a scene.
 *
 * Fetches its own copy rather than taking the one the view holds, because the
 * two differ and both are needed: the *stored* persona fills the fields (so a
 * blank field looks blank), and the *resolved* one -- with the fallback to the
 * project's player character already applied -- becomes the placeholder, which
 * is how you can see what a prompt would use without having to type it in.
 */
function PersonaEditor({ projectId, onClose, onSave }: {
  projectId: string;
  onClose: () => void;
  onSave: (next: RPPersona) => void;
}) {
  const [draft, setDraft] = useState<RPPersona>({ name: "", description: "", notes: "" });
  const [resolved, setResolved] = useState<RPPersona | null>(null);

  useEffect(() => {
    void api.rpPersona(projectId).then((out) => {
      setDraft(out.persona);
      setResolved(out.resolved);
    }).catch(() => undefined);
  }, [projectId]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 560 }} onClick={(e) => e.stopPropagation()}>
        <div className="head"><span className="title">Persona</span></div>
        <div className="content">
          <p className="hint">
            Who you are in a scene. Anything left blank falls back to the
            project's player character, so a project that has one needs nothing
            here.
          </p>
          <div className="field">
            <label>Name</label>
            <input value={draft.name} placeholder={resolved?.name ?? ""}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            <div className="hint">What the cast call you out loud.</div>
          </div>
          <div className="field">
            <label>Description</label>
            <textarea rows={4} value={draft.description}
              placeholder={resolved?.description ?? ""}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            <div className="hint">
              What the cast can see. Prose, not booru tags — this is read by the
              writer, never by the image pipeline.
            </div>
          </div>
          <div className="field">
            <label>Notes</label>
            <textarea rows={3} value={draft.notes}
              onChange={(e) => setDraft({ ...draft, notes: e.target.value })} />
            <div className="hint">
              Anything else true of you in every scene — age, work, how they know
              you.
            </div>
          </div>
        </div>
        <div className="foot">
          <button onClick={onClose}>Cancel</button>
          <button className="primary" onClick={() => onSave(draft)}>Save</button>
        </div>
      </div>
    </div>
  );
}

/**
 * The assembled prompt, verbatim.
 *
 * This is the tuning surface, and the reason it is in the first version rather
 * than added later: reading replies and guessing what the model was looking at
 * is how a prompt goes wrong and stays wrong.
 */
function PromptPreview({ preview, onClose }: { preview: RPPreview; onClose: () => void }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal rp-preview" style={{ maxWidth: 900 }}
        onClick={(e) => e.stopPropagation()}>
        <div className="head">
          <span className="title">What the model gets</span>
          <span className="spacer" />
          <span className="pill mono">{preview.token_estimate} tok</span>
          {preview.over_budget > 0 && (
            <span className="pill bad">{preview.over_budget} over</span>
          )}
          {preview.dropped_turns > 0 && (
            <span className="pill warn">{preview.dropped_turns} turns trimmed</span>
          )}
          <span className="pill">{preview.messages.length} messages</span>
        </div>
        <div className="content">
          <p className="hint">
            {preview.messages.filter((m) => m.role !== "system").length} turns after
            the system message, alternating, ending on yours — which is what makes
            stopping at your turn the model's default rather than something a
            regex has to enforce.
          </p>
          {preview.messages.map((m, i) => (
            <div key={i} className={`rp-preview-msg ${m.role}`}>
              <div className="rp-preview-role">{m.role}</div>
              <pre>{m.content}</pre>
            </div>
          ))}
        </div>
        <div className="foot">
          <button onClick={() => {
            void navigator.clipboard.writeText(
              preview.messages.map((m) => `### ${m.role}\n${m.content}`).join("\n\n"),
            ).catch(() => undefined);
          }}>Copy all</button>
          <button className="primary" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
