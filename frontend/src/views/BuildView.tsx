import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import BatchFillPanel from "../components/BatchFillPanel";
import MissingAudioPanel from "../components/MissingAudioPanel";
import { highlightRenpy } from "../lib/highlight";
import { useStore } from "../lib/store";

export default function BuildView() {
  const store = useStore();
  const [compiled, setCompiled] = useState<any>(null);
  const [file, setFile] = useState<string | null>(null);
  const [info, setInfo] = useState<any>(null);
  const [stale, setStale] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [webBusy, setWebBusy] = useState(false);
  const pollRef = useRef<number | undefined>(undefined);

  // The web build outlives this view -- leaving the Build tab unmounts it, and
  // an interval left running would poll a dead component forever.
  useEffect(() => () => window.clearInterval(pollRef.current), []);

  const refresh = async () => {
    if (!store.project) return;
    try {
      const [out, buildInfo, staleness] = await Promise.all([
        api.compile(store.project.id),
        api.buildInfo(store.project.id),
        api.staleness(store.project.id),
      ]);
      setCompiled(out);
      setInfo(buildInfo);
      setStale(staleness);
      setFile((current) =>
        current && out.files[current]
          ? current
          : (Object.keys(out.files)[0] ?? null),
      );
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    refresh();
  }, [store.project?.id, store.nodes.length]);

  // Ren'Py repacks every asset for a web build, so it is a job with a poll
  // rather than an await -- and build-info has to be re-read once it lands.
  const buildWeb = async () => {
    setWebBusy(true);
    setError(null);
    try {
      const { job_id } = await api.webBuild(store.project!.id, {});
      store.notify("web build queued — this takes a few minutes");
      store.refreshJobs();
      window.clearInterval(pollRef.current);
      pollRef.current = window.setInterval(async () => {
        // A poll that throws must stop the loop, not retry forever against a
        // server that is no longer answering.
        let job;
        try {
          job = await api.getJob(job_id);
        } catch (e) {
          window.clearInterval(pollRef.current);
          setWebBusy(false);
          setError(String(e));
          return;
        }
        if (job.status === "done") {
          window.clearInterval(pollRef.current);
          setWebBusy(false);
          store.notify("web build ready");
          refresh();
        } else if (["error", "cancelled"].includes(job.status)) {
          window.clearInterval(pollRef.current);
          setWebBusy(false);
          if (job.error) setError(job.error);
        }
      }, 3000);
    } catch (e) {
      setWebBusy(false);
      store.fail(String(e));
    }
  };

  if (!store.project) return null;

  const problems = info?.validation?.problems ?? [];
  const errors = problems.filter((p: any) => p.severity === "error");
  const staleCount = (stale?.images?.length ?? 0) + (stale?.audio?.length ?? 0);
  const recastCount = (stale?.audio ?? []).filter(
    (entry: any) => entry.reason === "voice clip changed",
  ).length;
  const web = info?.web;
  const playUrl = api.playUrl(store.project.id);

  return (
    <div className="view">
      <h2>Build</h2>
      <p className="lede">
        The IR compiles one way into <code>game/generated/</code>. Every other
        file in the Ren'Py project is yours and is never overwritten, so you can
        hand-edit the game alongside this tool.
      </p>

      {error && <div className="hint bad">{error}</div>}

      <div className="card">
        <div className="card-head">
          <span className="name">Target</span>
        </div>
        <div className="hint">
          {info?.project_dir && (
            <>
              Writing to <code>{info.project_dir}</code>.{" "}
            </>
          )}
          {info?.renpy_executable ? (
            <>
              Ren'Py SDK: <code>{info.renpy_executable}</code>
            </>
          ) : (
            <>
              No Ren'Py SDK configured — set <code>renpy_sdk_path</code> in
              Config to launch or lint.
            </>
          )}
        </div>
        <div className="hint" style={{ marginTop: 6 }}>
          {info?.renpy_executable
            ? <>One folder per project inside the SDK, named after the project.
                That is the directory the Ren'Py launcher scans, so this project
                appears in its list automatically — which is where the one-time
                Web support download lives. Rename the project and the folder
                follows; the old one is left behind for you to delete.</>
            : <>Set the SDK path in Config → services and each project gets its
                own folder inside it, listed by the Ren'Py launcher.</>}
        </div>

        {info?.sdk_game_dir && (
          <div className="hint warn" style={{ marginTop: 6 }}>
            ⚠ <code>{info.sdk_game_dir}</code> exists. Ren'Py prefers{" "}
            <code>basedir/game</code> over <code>launcher/game</code>, so while
            that folder is there the SDK boots it instead of the launcher.
            Delete it — it is left over from an older export and nothing in it
            belongs to Ren'Py. <b>Open Ren'Py launcher</b> refuses until you do:
            passing the launcher path to work around it would break the
            launcher's downloader, including Web support.
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="name">Preflight</span>
          <span className={`pill ${errors.length ? "bad" : "good"}`}>
            {errors.length} errors
          </span>
          <span className="pill warn">
            {problems.length - errors.length} warnings
          </span>
          {staleCount > 0 && (
            <span className="pill warn">{staleCount} stale assets</span>
          )}
          {compiled?.warnings?.length > 0 && (
            <span className="pill warn">
              {compiled.warnings.length} compiler warnings
            </span>
          )}
        </div>

        {staleCount > 0 && (
          <div style={{ marginBottom: 8 }}>
            <div className="hint warn">
              {staleCount} assets no longer match what they were generated
              from. Regenerate them, or the build will ship voice lines that do
              not match the words on screen.
            </div>
            {recastCount > 0 && (
              <div className="hint warn">
                {recastCount} of them are voice lines whose text is unchanged —
                their character was recast, so they still carry the old timbre.
              </div>
            )}
            <button
              style={{ marginTop: 6 }}
              onClick={async () => {
                await api.regenerateStale(store.project!.id);
                store.notify("stale regeneration queued");
                store.refreshJobs();
              }}
            >
              Regenerate all stale assets
            </button>
          </div>
        )}

        {problems.slice(0, 20).map((problem: any, index: number) => (
          <div
            key={index}
            className={`hint ${problem.severity === "error" ? "bad" : "warn"}`}
          >
            {problem.severity === "error" ? "✗" : "⚠"} {problem.message}
            {store.nodes.some((n) => n.id === problem.node_id) && (
              <button
                className="tiny ghost"
                onClick={() => {
                  store.select(problem.node_id);
                  store.setView("canvas");
                }}
              >
                go
              </button>
            )}
          </div>
        ))}
        {compiled?.warnings?.map((warning: string, index: number) => (
          <div key={index} className="hint warn">
            ⚠ {warning}
          </div>
        ))}
        {problems.length === 0 && !compiled?.warnings?.length && (
          <div className="hint">Nothing to flag.</div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <span className="name">Actions</span>
          {compiled?.stats && (
            <span className="hint">
              {compiled.stats.nodes} nodes · {compiled.stats.labels} labels ·{" "}
              {compiled.stats.scenes} scenes
            </span>
          )}
        </div>
        <div className="row">
          <button
            className="primary"
            title="Compiles the graph and writes game/generated/ into the Ren'Py project folder, copying images and voice lines across. Your script.rpy, custom.rpy and options.rpy are never touched."
            onClick={async () => {
              await api.exportProject(store.project!.id, { copy_assets: true });
              store.notify("export queued");
              store.refreshJobs();
            }}
          >
            Export .rpy + assets
          </button>
          <button
            title="The same export, then opens the game in Ren'Py so you can play it straight away."
            onClick={async () => {
              await api.exportProject(store.project!.id, {
                copy_assets: true,
                launch: true,
              });
              store.notify("export + launch queued");
              store.refreshJobs();
            }}
          >
            Export and launch
          </button>
          <button
            title="Runs Ren'Py's own checker over the exported script — missing images, bad labels, syntax it will not load. Needs the SDK path set under Config → services."
            onClick={async () => {
              await api.lint(store.project!.id);
              store.notify("renpy lint queued");
              store.refreshJobs();
            }}
          >
            Run renpy lint
          </button>
          <button
            title="Queues a TTS job for every voiced line that has no audio yet. Roughly 5 seconds a line on one 4090 — start it and leave it."
            onClick={async () => {
              const out = await api.voiceAll(store.project!.id, true);
              store.notify(`${out.count} voice jobs queued`);
              store.refreshJobs();
            }}
          >
            Voice every missing line
          </button>
          <button
            disabled={!info?.renpy_executable}
            title="The SDK's own UI — Build Distributions, Web support, preferences"
            onClick={async () => {
              const out = await api.openRenpyLauncher();
              store.notify(out.launched
                ? "Ren'Py launcher opening…"
                : (out.reason ?? "could not open the launcher"));
            }}
          >
            Open Ren'Py launcher
          </button>
        </div>
        <div className="hint" style={{ marginTop: 6 }}>
          A fully voiced 2,000-line VN is an overnight batch, not an interactive
          wait — roughly 3 hours of TTS and 1.5 hours of images on one 4090,
          before retries.
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <span className="name">Play in browser</span>
          {web?.built ? (
            <span className="pill good">
              built · {(web.bytes / 1e6).toFixed(1)} MB
            </span>
          ) : (
            <span className="pill">not built yet</span>
          )}
        </div>
        <div className="row">
          <button className="primary" disabled={webBusy}
            title="Packages the game as HTML5 so it runs in a browser with no Ren'Py install. Needs the SDK's web support downloaded once from the launcher."
            onClick={buildWeb}>
            {webBusy ? "Building…" : "Build for web (HTML)"}
          </button>
          <button
            disabled={!web?.built}
            title="Opens the web build in a new tab. This is the real game, not the in-browser preview player."
            onClick={() => window.open(playUrl, "_blank", "noopener")}
          >
            ▶ Play
          </button>
          {web?.built && (
            <button
              className="ghost"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(playUrl);
                  store.notify("link copied");
                } catch {
                  store.notify(playUrl); // no clipboard API on plain http
                }
              }}
            >
              Copy link
            </button>
          )}
        </div>
        {web?.built && (
          <div className="hint" style={{ marginTop: 6 }}>
            Open <code>{playUrl}</code> on a phone on the same network — the
            build is served from this server, so nothing else needs installing.
            Built {new Date(web.built_at).toLocaleString()}.
          </div>
        )}
        {info?.renpy_executable && web && !web.support && (
          <div className="hint warn" style={{ marginTop: 6 }}>
            ⚠ This SDK has no <code>web/</code> folder — Ren'Py's web support is
            a one-time download only the launcher can do. Open the launcher,
            pick your project in the left list, click <b>Web</b> in the actions
            column (next to <i>Build Distributions</i>), and accept the download
            prompt.
            <div style={{ marginTop: 6 }}>
              <button
                className="tiny"
                onClick={async () => {
                  const out = await api.openRenpyLauncher();
                  store.notify(
                    out.launched
                      ? "Ren'Py launcher opening…"
                      : (out.reason ?? "could not open the launcher"),
                  );
                }}
              >
                Open Ren'Py launcher
              </button>
            </div>
          </div>
        )}
        <div className="hint" style={{ marginTop: 6 }}>
          Exports the project, then runs the launcher's <code>web_build</code>{" "}
          command straight into the folder we serve. Ren'Py repacks every asset,
          so a voiced project takes a few minutes.
        </div>
      </div>

      <BatchFillPanel />
      <MissingAudioPanel />

      <h3>Compiled output</h3>
      <div className="split">
        <div className="left">
          <div className="filetree">
            {Object.keys(compiled?.files ?? {})
              .sort()
              .map((name) => (
                <button
                  key={name}
                  className={file === name ? "active" : ""}
                  onClick={() => setFile(name)}
                >
                  {name.replace("game/generated/", "")}
                </button>
              ))}
          </div>
        </div>
        <div className="right">
          {file && compiled?.files?.[file] && (
            <pre
              className="script"
              dangerouslySetInnerHTML={{
                __html: highlightRenpy(compiled.files[file]),
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}
