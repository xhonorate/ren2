import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Paint the region of a render to regenerate.
 *
 * Two canvases, deliberately. The visible one shows the image with the painted
 * area tinted so the author can see what they are selecting against the
 * picture underneath; the hidden one holds the actual mask, black and white at
 * the source image's true pixel dimensions. Compositing a tint over the image
 * and then trying to extract the mask back out of it is the version that does
 * not work -- the tint has to be translucent to be useful, and a translucent
 * mask is a partial denoise nobody asked for.
 *
 * White is the region to regenerate. `SetLatentNoiseMask` on the backend
 * confines the sampler to it and carries everything else through untouched.
 */
export default function MaskPainter({ src, onChange }: {
  src: string;
  /** The mask as a PNG data URL, or "" once nothing is painted. */
  onChange: (mask: string) => void;
}) {
  const viewRef = useRef<HTMLCanvasElement | null>(null);
  const maskRef = useRef<HTMLCanvasElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const drawing = useRef(false);
  const last = useRef<{ x: number; y: number } | null>(null);

  const [brush, setBrush] = useState(64);
  const [erasing, setErasing] = useState(false);
  const [ready, setReady] = useState(false);
  const [painted, setPainted] = useState(false);

  // Load the source and size both canvases to its true pixels. The mask has to
  // match the image the sampler will encode, not the box it is displayed in.
  useEffect(() => {
    let cancelled = false;
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      if (cancelled) return;
      imageRef.current = img;
      for (const canvas of [viewRef.current, maskRef.current]) {
        if (!canvas) continue;
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
      }
      const mask = maskRef.current?.getContext("2d");
      if (mask) {
        mask.fillStyle = "#000";
        mask.fillRect(0, 0, img.naturalWidth, img.naturalHeight);
      }
      setReady(true);
      setPainted(false);
      onChange("");
      repaint();
    };
    img.src = src;
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  /** Redraw the visible canvas: the image, then the mask as a tint over it. */
  const repaint = useCallback(() => {
    const view = viewRef.current;
    const maskCanvas = maskRef.current;
    const img = imageRef.current;
    const ctx = view?.getContext("2d");
    if (!view || !ctx || !img || !maskCanvas) return;
    ctx.clearRect(0, 0, view.width, view.height);
    ctx.drawImage(img, 0, 0);
    ctx.save();
    ctx.globalAlpha = 0.45;
    // The mask is white-on-black; drawing it with `lighter` tints only the
    // painted part and leaves the black as a no-op.
    ctx.globalCompositeOperation = "lighter";
    ctx.drawImage(maskCanvas, 0, 0);
    ctx.restore();
  }, []);

  /** Canvas pixel coordinates for a pointer event, whatever the display size. */
  const at = (e: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = e.currentTarget;
    const box = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - box.left) * (canvas.width / box.width),
      y: (e.clientY - box.top) * (canvas.height / box.height),
    };
  };

  const stroke = (from: { x: number; y: number } | null,
                  to: { x: number; y: number }) => {
    const ctx = maskRef.current?.getContext("2d");
    if (!ctx) return;
    // Erasing paints black rather than clearing, so the mask stays fully
    // opaque -- a cleared pixel is transparent, and a transparent pixel read
    // back as red is zero either way, but only by accident.
    ctx.strokeStyle = ctx.fillStyle = erasing ? "#000" : "#fff";
    ctx.lineCap = ctx.lineJoin = "round";
    ctx.lineWidth = brush;
    ctx.beginPath();
    if (from) {
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x, to.y);
      ctx.stroke();
    } else {
      ctx.arc(to.x, to.y, brush / 2, 0, Math.PI * 2);
      ctx.fill();
    }
    repaint();
  };

  const commit = useCallback(() => {
    const canvas = maskRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    // Nothing painted is not an empty mask -- it is "no mask", which the
    // backend reads as "refine the whole frame". An all-black PNG would
    // instead tell the sampler to change nothing at all.
    const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
    let any = false;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i] > 8) { any = true; break; }
    }
    setPainted(any);
    onChange(any ? canvas.toDataURL("image/png") : "");
  }, [onChange]);

  const clear = () => {
    const canvas = maskRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    repaint();
    setPainted(false);
    onChange("");
  };

  return (
    <div className="mask-painter">
      <canvas
        ref={viewRef}
        className="mask-canvas"
        style={{ cursor: ready ? "crosshair" : "wait" }}
        onPointerDown={(e) => {
          if (!ready) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          drawing.current = true;
          const p = at(e);
          last.current = p;
          stroke(null, p);
        }}
        onPointerMove={(e) => {
          if (!drawing.current) return;
          const p = at(e);
          stroke(last.current, p);
          last.current = p;
        }}
        onPointerUp={(e) => {
          if (!drawing.current) return;
          e.currentTarget.releasePointerCapture(e.pointerId);
          drawing.current = false;
          last.current = null;
          commit();
        }}
      />
      <canvas ref={maskRef} style={{ display: "none" }} />
      <div className="row" style={{ gap: 8, marginTop: 6, flexWrap: "wrap" }}>
        <button className={`tiny ${erasing ? "ghost" : "primary"}`}
          onClick={() => setErasing(false)}>Paint</button>
        <button className={`tiny ${erasing ? "primary" : "ghost"}`}
          onClick={() => setErasing(true)}>Erase</button>
        <label className="row" style={{ gap: 6 }}>
          <span className="hint">Brush</span>
          <input type="range" min={8} max={256} step={4} value={brush}
            style={{ width: 110 }}
            onChange={(e) => setBrush(Number(e.target.value))} />
          <span className="hint mono" style={{ width: 30 }}>{brush}</span>
        </label>
        <button className="tiny ghost" onClick={clear}>Clear</button>
        <span className="hint" style={{ marginLeft: "auto" }}>
          {painted ? "region painted" : "whole frame"}
        </span>
      </div>
    </div>
  );
}
