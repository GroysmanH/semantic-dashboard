import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";

export interface ExportItem {
  label: string;
  disabled?: boolean;
  run: () => void | Promise<void>;
}

export default function ExportMenu({
  label,
  items,
  align = "end",
  onError,
}: {
  label: string;
  items: ExportItem[];
  align?: "start" | "end";
  onError?: (error: unknown) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const disabled = items.length === 0 || items.every((item) => item.disabled);

  useEffect(() => {
    if (!open) return;
    menuRef.current
      ?.querySelector<HTMLButtonElement>('[role="menuitem"]:not(:disabled)')
      ?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      buttonRef.current?.focus();
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const run = (item: ExportItem) => {
    setOpen(false);
    buttonRef.current?.focus();
    try {
      void Promise.resolve(item.run()).catch((error) => onError?.(error));
    } catch (error) {
      onError?.(error);
    }
  };

  const moveFocus = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const enabled = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>(
        '[role="menuitem"]:not(:disabled)',
      ) ?? [],
    );
    if (!enabled.length) return;
    event.preventDefault();
    const current = enabled.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "Home") enabled[0].focus();
    else if (event.key === "End") enabled[enabled.length - 1].focus();
    else if (event.key === "ArrowDown") {
      enabled[(current + 1 + enabled.length) % enabled.length].focus();
    } else {
      enabled[(current - 1 + enabled.length) % enabled.length].focus();
    }
  };

  return (
    <div className="export-menu" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="export-menu-button"
        aria-label={label}
        aria-haspopup="menu"
        aria-controls={menuId}
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        <span>Export</span>
        <span className="export-chevron" aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div
          id={menuId}
          ref={menuRef}
          className={`export-menu-popover align-${align}`}
          role="menu"
          aria-label={label}
          onKeyDown={moveFocus}
        >
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={() => run(item)}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
