import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

/**
 * A confirmation the application owns.
 *
 * `window.confirm` was doing this job and doing three things wrong: it is
 * styled by the browser rather than by us, it announces the origin
 * ("localhost:5173 says") which reads like a security prompt, and it
 * blocks the JavaScript thread, so nothing behind it can finish loading
 * while somebody decides.
 *
 * The important part is not the styling. A destructive prompt is only
 * worth showing if it is read, and one that looks like every other browser
 * dialog gets dismissed on reflex. This one names what will happen in the
 * button, in the page's own voice.
 *
 * Rendered into <body> rather than where it is written. `position: fixed`
 * is fixed to the nearest ancestor carrying a transform, not to the
 * viewport, and the assistant panel carries one because it slides -- so a
 * dialog raised from inside it centred itself in the panel. A portal is
 * the fix that does not require the panel to stop sliding.
 */
export default function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  destructive = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body?: string;
  /** Says what happens, not "OK". A button labelled OK is a button nobody
   *  read the sentence above. */
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const returnTo = useRef<Element | null>(null);

  useEffect(() => {
    if (!open) return;
    returnTo.current = document.activeElement;
    confirmRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // Back where they were. A dialog that drops focus on <body> sends
      // the next Tab to the top of the page.
      (returnTo.current as HTMLElement | null)?.focus?.();
    };
  }, [open, onCancel]);

  if (!open) return null;

  return createPortal(
    <div className="modal-scrim" onPointerDown={onCancel}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        // The scrim closes on a click; the dialog is not the scrim.
        onPointerDown={(event) => event.stopPropagation()}
      >
        <h2>{title}</h2>
        {body && <p>{body}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={destructive ? "danger" : "primary"}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
