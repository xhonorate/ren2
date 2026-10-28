import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useStore } from "../lib/store";
import type { IRNode, Variant } from "../lib/types";

/** Flip between takes. Regenerating never destroys the previous one (doc S2).
 *
 *  Image takes get a thumbnail grid rather than a row of "seed 41983" labels:
 *  a batch render exists so you can compare four candidates and keep one, and
 *  a seed number is not something anyone can compare. Text and voice takes stay
 *  a list, where their content is the thing worth reading -- and a voice take
 *  gets a play button, because a re-take is picked by ear and clicking a take
 *  to hear it would also put it on the node.
 */
export default function VariantStrip({ node }: { node: IRNode }) {
  const store = useStore();
  const [variants, setVariants] = useState<Variant[]>(node.variants ?? []);
  const [active, setActive] = useState<string | null>(node.active_variant);

  const player = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState<string | null>(null);

  useEffect(() => {
    setVariants(node.variants ?? []);
    setActive(node.active_variant);
  }, [node.id, node.variants?.length, node.active_variant]);

  // Stop a take when the inspector moves to another node.
  useEffect(() => () => {
    player.current?.pause();
    player.current = null;
    setPlaying(null);
  }, [node.id]);

  if (!variants.length) return null;

  const listen = (hash: string) => {
    player.current?.pause();
    if (playing === hash) {
      player.current = null;
      setPlaying(null);
      return;
    }
    const audio = new Audio(api.assetUrl(hash));
    audio.onended = () => setPlaying((now) => (now === hash ? null : now));
    audio.play().catch((e) => {
      setPlaying(null);
      store.fail(String(e));
    });
    player.current = audio;
    setPlaying(hash);
  };

  const apply = async (variant: Variant) => {
    try {
      const updated = await api.applyVariant(store.project!.id, node.id, variant.id);
      setActive(updated.active_variant);
      await store.refreshGraph();
    } catch (e) {
      store.fail(String(e));
    }
  };

  // The take's own position is kept as a number so it still reads as "#3 of 7"
  // in the grid, which is ordered newest-first rather than chronologically.
  const numbered = variants.map((variant, index) => ({ variant, index }));
  const images = numbered.filter(({ variant }) => showable(variant));
  const rest = numbered.filter(({ variant }) => !showable(variant));

  return (
    <>
      <div className="section-title">Takes ({variants.length})</div>

      {images.length > 0 && (
        <>
          {/* Newest first: the batch you just rendered is what you are here to
              choose from, and it would otherwise be at the bottom of a grid of
              everything you have ever rolled on this node. */}
          <div className="take-grid">
            {[...images].reverse().map(({ variant, index }) => (
              <button
                key={variant.id}
                className={`take ${active === variant.id ? "on" : ""}`}
                title={describeImage(variant, index, variants.length)}
                onClick={() => apply(variant)}
              >
                <img src={api.assetUrl(variant.asset_hash!)} loading="lazy"
                  decoding="async" alt={`take ${index + 1}`} />
                <span className="num">
                  #{index + 1}
                  {variant.payload?.batch_index !== undefined
                    && ` · ${Number(variant.payload.batch_index) + 1}/${variant.payload.batch}`}
                </span>
              </button>
            ))}
          </div>
          <div className="hint">
            Click one to put it on the node. Nothing is deleted — every take
            stays in the Gallery, and the one on the node is just the one you
            picked.
          </div>
        </>
      )}

      {rest.length > 0 && (
        <div className="list">
          {rest.map(({ variant, index }) => (
            <div
              key={variant.id}
              className={`item ${active === variant.id ? "active" : ""}`}
              onClick={() => apply(variant)}
              title={[
                variant.meta?.model && `model: ${variant.meta.model}`,
                variant.meta?.seed != null && `seed: ${variant.meta.seed}`,
                variant.meta?.timestamp,
              ].filter(Boolean).join("\n")}
            >
              <span className="pill">{variant.kind}</span>
              {variant.kind === "audio" && variant.asset_hash && (
                <button className="tiny ghost"
                  title={playing === variant.asset_hash
                    ? "Stop" : "Listen without putting it on the node"}
                  onClick={(e) => {
                    e.stopPropagation();
                    listen(variant.asset_hash!);
                  }}>
                  {playing === variant.asset_hash ? "■" : "▶"}
                </button>
              )}
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {describe(variant)}
              </span>
              <span className="sub">#{index + 1}</span>
            </div>
          ))}
        </div>
      )}

      <div className="hint">
        Each take records model, params, seed and prompt hash, so you can diff
        them and roll back.
      </div>
    </>
  );
}

/** An image take with a file behind it -- the only kind that can be a picture.
 *  An image variant whose asset was force-deleted from the gallery falls back
 *  to the list, where it still reads and still applies. */
function showable(variant: Variant): boolean {
  return variant.kind === "image" && !!variant.asset_hash;
}

/** The tooltip on an image take. A batch index is spelled out because the seed
 *  on its own does not reproduce one image of a batch -- ComfyUI draws the
 *  whole batch's noise from one generator, so it takes the seed, the batch size
 *  and the position to get this picture back. */
function describeImage(variant: Variant, index: number, total: number): string {
  return [
    `take ${index + 1} of ${total}`,
    variant.meta?.seed != null && `seed: ${variant.meta.seed}`,
    variant.payload?.batch_index !== undefined
      && `batch position ${Number(variant.payload.batch_index) + 1} of ${variant.payload.batch}`,
    variant.meta?.timestamp,
  ].filter(Boolean).join("\n");
}

function describe(variant: Variant): string {
  if (variant.kind === "text") return variant.payload?.text || "(no text)";
  if (variant.kind === "image") return `seed ${variant.meta?.seed ?? "?"}`;
  if (variant.kind === "audio") {
    const payload = variant.payload ?? {};
    const resolved = payload.resolved;
    const top = resolved?.vec
      ? Object.entries(resolved.vec)
          .filter(([, v]) => Number(v) > 0)
          .map(([k, v]) => `${k} ${Number(v).toFixed(2)}`)
          .join(", ")
      : "";
    return [
      payload.takes && `take ${Number(payload.take_index) + 1}/${payload.takes}`,
      top,
      // The delivery rules that shaped the read ("trailing", "flat").
      resolved?.delivery?.length && resolved.delivery.join(" + "),
    ].filter(Boolean).join(" · ") || "voice take";
  }
  return "";
}
