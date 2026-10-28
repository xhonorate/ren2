import type { ReactNode } from "react";

/**
 * Generic "are you sure?" modal for a destructive action -- delete a
 * character, clear a LoRA pool, delete a location, delete a node. One shared
 * component so every confirm dialog in the app looks and behaves the same,
 * rather than each tab growing its own copy.
 *
 * For a confirmation that needs more than a yes/no (e.g. the node delete
 * modal's cascade-or-reconnect choice), pass that as `children`; the confirm
 * button still does exactly one thing when clicked; the caller decides what.
 */
export default function ConfirmDialog({
  title,
  children,
  confirmLabel = "Delete",
  cancelLabel = "Cancel",
  danger = true,
  maxWidth = 420,
  busy = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  children?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  maxWidth?: number;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" style={{ maxWidth }} onClick={(e) => e.stopPropagation()}>
        <div className="head">
          <span className="title">{title}</span>
        </div>
        {children && <div className="content">{children}</div>}
        <div className="foot">
          <button onClick={onCancel} disabled={busy}>{cancelLabel}</button>
          <button
            className={`primary ${danger ? "danger" : ""}`}
            disabled={busy}
            onClick={onConfirm}
          >
            {busy ? "…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
