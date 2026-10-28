import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { IRNode } from "../lib/types";

/** Voice a say line, or re-take one that already has a voice.
 *
 *  The first voicing is a single take: most reads are fine, and rolling
 *  several for every line would multiply the wait for nothing. Pressing it
 *  again means that read was wrong, so a re-take rolls `voice_retakes` seeds
 *  at once and every one lands in the take strip to be picked by ear.
 */
export default function VoiceLineButton({ node, busy, onGen, className }: {
  node: IRNode | undefined;
  busy: boolean;
  onGen: (fn: () => Promise<{ job_id: string }>) => Promise<void>;
  className?: string;
}) {
  const store = useStore();
  if (!node) return null;
  const voiced = !!node.data?.voice_path;
  const takes = voiced ? Math.max(1, Number(store.settings.voice_retakes ?? 3) || 1) : 1;

  return (
    <button className={className} disabled={busy}
      title={voiced
        ? `Voice it again as ${takes} takes to pick from in the take strip — `
          + "the current take is kept"
        : "Voice this line"}
      onClick={() => onGen(() => api.voiceLine(store.project!.id, node.id, takes))}>
      {voiced ? `⟳ Re-take voice${takes > 1 ? ` ×${takes}` : ""}` : "Voice line"}
    </button>
  );
}
