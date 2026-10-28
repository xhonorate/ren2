import { api } from "../lib/api";
import { useStore } from "../lib/store";

/**
 * The job list. Rendered as a popout under the navbar's jobs button rather
 * than docked to the canvas, so it is reachable from every view and never
 * sits on top of the graph. Open/close state lives in App -- the button needs
 * it too -- so this component is purely the panel.
 */
export default function JobDock({ onClose }: { onClose: () => void }) {
  const store = useStore();

  const active = store.jobs.filter((j) => ["queued", "claimed", "running"].includes(j.status));
  const recent = store.jobs.slice(0, 24);

  if (!store.project) return null;

  return (
    <div className="jobs-popout" role="dialog" aria-label="Jobs">
      <div className="head">
        <strong>Jobs</strong>
        {active.length > 0
          ? <span className="pill">{active.length} running</span>
          : <span className="pill good">idle</span>}
        <span style={{ marginLeft: "auto" }} />
        <button className="tiny ghost" onClick={() => {
          api.clearJobs(store.project!.id).then(() => store.refreshJobs());
        }}>Clear</button>
        <button className="tiny ghost" title="Close" onClick={onClose}>✕</button>
      </div>
      <div className="scroll">
        {recent.length === 0 && <div className="hint">Nothing queued yet.</div>}
        {recent.map((job) => (
          <div key={job.id} className={`job ${job.status}`}>
            <div className="top">
              <span className="kind">{job.kind}</span>
              <span style={{ marginLeft: "auto" }} />
              <span className={`pill ${job.status === "done" ? "good"
                : job.status === "error" ? "bad" : ""}`}>{job.status}</span>
              {["queued", "claimed", "running"].includes(job.status) && (
                <button className="tiny ghost" title="cancel"
                  onClick={() => api.cancelJob(job.id).then(() => store.refreshJobs())}>×</button>
              )}
            </div>
            {job.label && <div style={{ color: "var(--muted)", marginTop: 2 }}>{job.label}</div>}
            {["running", "claimed"].includes(job.status) && (
              <div className="bar"><div style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>
            )}
            {job.error && <div className="err">{job.error.split("\n")[0]}</div>}
            {job.node_id && (
              <button className="tiny ghost" style={{ marginTop: 3 }}
                onClick={() => {
                  store.setView("canvas");
                  store.select(job.node_id!);
                  onClose();
                }}>go to node</button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
