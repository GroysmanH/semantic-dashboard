import { useEffect, useId, useRef } from "react";

export default function SqlPanel({
  sql,
  open,
  onToggle,
  onHeightChange,
}: {
  sql: string;
  open: boolean;
  onToggle: () => void;
  onHeightChange: (height: number) => void;
}) {
  const panelId = useId();
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open || !drawerRef.current) {
      onHeightChange(0);
      return;
    }

    const drawer = drawerRef.current;
    const measure = () => onHeightChange(Math.ceil(drawer.getBoundingClientRect().height));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(drawer);
    return () => {
      observer.disconnect();
      onHeightChange(0);
    };
  }, [onHeightChange, open]);

  return (
    <section className={`sql ${open ? "open" : ""}`} data-card-control>
      <button
        className="sql-toggle"
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={onToggle}
      >
        <span className="sql-chevron" aria-hidden="true">›</span>
        <span className="eyebrow">Compiled SQL</span>
      </button>
      {open && (
        <div className="sql-drawer" id={panelId} ref={drawerRef}>
          <pre>{sql}</pre>
        </div>
      )}
    </section>
  );
}
