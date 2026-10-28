import { useEffect, useRef, useState } from "react";
import AuthGate from "./components/AuthGate";
import Canvas from "./components/Canvas";
import Inspector from "./components/Inspector";
import JobDock from "./components/JobDock";
import Sidebar from "./components/Sidebar";
import { useStore, type View } from "./lib/store";
import { useIsNarrow } from "./lib/useMediaQuery";
import BuildView from "./views/BuildView";
import CharactersView from "./views/CharactersView";
import ConfigView from "./views/ConfigView";
import GalleryView from "./views/GalleryView";
import PreviewPlayer from "./views/PreviewPlayer";
import RoutesView from "./views/RoutesView";
import RoleplayView from "./views/RoleplayView";
import ScriptView from "./views/ScriptView";
import SettingView from "./views/SettingView";

const TABS: { id: View; label: string; short: string }[] = [
  { id: "canvas", label: "Canvas", short: "Graph" },
  { id: "script", label: "Script", short: "Script" },
  { id: "roleplay", label: "Roleplay", short: "RP" },
  { id: "preview", label: "Play", short: "Play" },
  { id: "characters", label: "Characters", short: "Cast" },
  { id: "routes", label: "Routes", short: "Routes" },
  { id: "setting", label: "Setting", short: "Setting" },
  { id: "gallery", label: "Gallery", short: "Art" },
  { id: "build", label: "Build", short: "Build" },
  { id: "config", label: "Config", short: "Setup" },
];

/** Views that get the left palette / right inspector on a wide screen. */
const WITH_SIDEBAR: View[] = ["canvas"];
const WITH_INSPECTOR: View[] = ["canvas", "script"];

/** Sentinel value for the "new project" entry in the project picker. */
const NEW_PROJECT = "__new__";

/**
 * The gate is the whole app's root. Workspace is a separate component so it
 * only mounts once unlocked -- its init effect fetches immediately, and
 * mounting it while locked is what caused the 401 refresh loop.
 */
export default function App() {
  return (
    <AuthGate>
      <Workspace />
    </AuthGate>
  );
}

function Workspace() {
  const store = useStore();
  const narrow = useIsNarrow();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [drawer, setDrawer] = useState<"none" | "sidebar" | "inspector">(
    "none",
  );
  const [jobsOpen, setJobsOpen] = useState(false);
  const jobsAnchor = useRef<HTMLDivElement>(null);

  useEffect(() => {
    store.init();
  }, []);

  // On a phone, picking a node should bring up its editor rather than leaving
  // the selection invisible behind the canvas.
  useEffect(() => {
    if (narrow && store.selected && WITH_INSPECTOR.includes(store.view)) {
      setDrawer("inspector");
    }
  }, [store.selected]);

  useEffect(() => {
    setDrawer("none");
  }, [store.view, narrow]);

  // A jump from elsewhere (the gallery's "used by", say) asks for the
  // selection to be *shown*, which on a phone means the editor drawer. Placed
  // after the reset above so it wins for a jump that also changed the view --
  // effects run in declaration order.
  useEffect(() => {
    if (!store.focusRequest) return;
    if (narrow && store.selected && WITH_INSPECTOR.includes(store.view)) {
      setDrawer("inspector");
    }
  }, [store.focusRequest]);

  // The jobs popout is anchored to its navbar button, so it dismisses the way
  // a menu does: a click anywhere outside it, or Escape.
  useEffect(() => {
    if (!jobsOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!jobsAnchor.current?.contains(e.target as Node)) setJobsOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setJobsOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [jobsOpen]);

  const activeJobs = store.jobs.filter((j) =>
    ["queued", "claimed", "running"].includes(j.status),
  ).length;
  const errorCount = store.problems.filter(
    (p) => p.severity === "error",
  ).length;

  const showSidebar = WITH_SIDEBAR.includes(store.view);
  const showInspector = WITH_INSPECTOR.includes(store.view);

  const body = () => {
    if (!store.project) {
      return (
        <div className="empty-state">
          <h2>No project open</h2>
          <p>
            Create one to start. The graph is the source of truth; Ren'Py is a
            one-way compile target.
          </p>
          <button className="primary" onClick={() => setCreating(true)}>
            New project
          </button>
        </div>
      );
    }
    switch (store.view) {
      case "canvas":
        return <Canvas />;
      case "script":
        return <ScriptView />;
      case "roleplay":
        return <RoleplayView />;
      case "preview":
        return <PreviewPlayer />;
      case "characters":
        return <CharactersView />;
      case "routes":
        return <RoutesView />;
      case "setting":
        return <SettingView />;
      case "gallery":
        return <GalleryView />;
      case "build":
        return <BuildView />;
      default:
        return <ConfigView />;
    }
  };

  return (
    <div className={`app ${narrow ? "narrow" : ""}`}>
      <header className="topbar">
        {narrow && store.project && showSidebar && (
          <button
            className="icon-btn"
            title="Add nodes"
            onClick={() => setDrawer(drawer === "sidebar" ? "none" : "sidebar")}
          >
            ☰
          </button>
        )}
        {!narrow && <span className="brand">VN Authoring Tool</span>}

        <select
          className="project-select"
          value={store.project?.id ?? ""}
          onChange={(e) => {
            if (e.target.value === NEW_PROJECT) {
              setCreating(true);
              return;
            }
            if (e.target.value) store.openProject(e.target.value);
          }}
        >
          <option value="">— no project —</option>
          {store.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
          <option value={NEW_PROJECT}>＋ New project…</option>
        </select>

        {store.project &&
          (narrow ? (
            <select
              className="view-select"
              value={store.view}
              onChange={(e) => store.setView(e.target.value as View)}
            >
              {TABS.map((tab) => (
                <option key={tab.id} value={tab.id}>
                  {tab.label}
                </option>
              ))}
            </select>
          ) : (
            <nav className="tabs scroll-x">
              {TABS.map((tab) => (
                <button
                  key={tab.id}
                  className={store.view === tab.id ? "active" : ""}
                  onClick={() => store.setView(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </nav>
          ))}

        <span className="spacer" />

        {store.project && (
          <>
            {!narrow && (
              <span className="pill">{store.nodes.length} nodes</span>
            )}
            <div className="jobs-anchor" ref={jobsAnchor}>
              <button
                className={`ghost icon-btn jobs-btn ${jobsOpen ? "on" : ""}`}
                aria-expanded={jobsOpen}
                title={
                  activeJobs > 0
                    ? `Jobs — ${activeJobs} running`
                    : "Jobs — idle"
                }
                onClick={() => setJobsOpen(!jobsOpen)}
              >
                ⚙
                {activeJobs > 0 && (
                  <span className="job-badge">{activeJobs}</span>
                )}
              </button>
              {jobsOpen && <JobDock onClose={() => setJobsOpen(false)} />}
            </div>
            {!narrow && (
              <span
                className={`pill ${errorCount ? "bad" : "good"}`}
                title={`${errorCount} validation errors`}
              >
                {errorCount} errors
              </span>
            )}
          </>
        )}
      </header>

      <div className="body">
        {!narrow && store.project && showSidebar && <Sidebar />}
        <main className="main">{body()}</main>
        {!narrow && store.project && showInspector && <Inspector />}
      </div>

      {narrow && drawer !== "none" && (
        <>
          <div className="drawer-scrim" onClick={() => setDrawer("none")} />
          <div className={`drawer ${drawer === "sidebar" ? "left" : "right"}`}>
            <div className="drawer-head">
              <strong>
                {drawer === "sidebar" ? "Add & find" : "Inspector"}
              </strong>
              <span className="spacer" />
              <button className="icon-btn" onClick={() => setDrawer("none")}>
                ✕
              </button>
            </div>
            <div className="drawer-body">
              {drawer === "sidebar" ? <Sidebar /> : <Inspector />}
            </div>
          </div>
        </>
      )}

      {store.toast && <div className="toast">{store.toast}</div>}

      {/* item 1: "new project" lives in the project picker; no floating button */}
      {creating && (
        <div className="modal-backdrop" onClick={() => setCreating(false)}>
          <div
            className="modal"
            style={{ maxWidth: 400 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="head">
              <span className="title">New project</span>
            </div>
            <div className="content">
              <div className="field">
                <label>Name</label>
                <input
                  autoFocus
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && name.trim()) {
                      store.createProject(name.trim());
                      setName("");
                      setCreating(false);
                    }
                  }}
                />
              </div>
            </div>
            <div className="foot">
              <button onClick={() => setCreating(false)}>Cancel</button>
              <button
                className="primary"
                disabled={!name.trim()}
                onClick={() => {
                  store.createProject(name.trim());
                  setName("");
                  setCreating(false);
                }}
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
