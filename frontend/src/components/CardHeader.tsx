import { useEffect, useRef, useState } from "react";
import type { Render } from "../api/types.gen";

function clockOf(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function ageMinutes(iso: string | null | undefined): number {
  if (!iso) return Infinity;
  return (Date.now() - new Date(iso).getTime()) / 60000;
}

function RefreshIcon({ busy }: { busy: boolean }) {
  return (
    <svg className={busy ? "spin" : ""} viewBox="0 0 20 20" aria-hidden="true">
      <path d="M15.4 6.2A6.3 6.3 0 1 0 16 12h-1.8a4.5 4.5 0 1 1-.4-4.3L11.5 10H18V3.5l-2.6 2.7Z" />
    </svg>
  );
}

function UndoIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M8 5.2 3.2 10 8 14.8v-3.2h3.3c2.5 0 4.2 1.1 5.5 3.4-.2-5.3-2-7.6-5.5-7.6H8V5.2Z" />
    </svg>
  );
}

function MenuIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="4" cy="10" r="1.5" />
      <circle cx="10" cy="10" r="1.5" />
      <circle cx="16" cy="10" r="1.5" />
    </svg>
  );
}

export default function CardHeader({
  title,
  state,
  render,
  ttlSeconds,
  canUndo,
  busy,
  onSelect,
  onMoveIntent,
  onRefresh,
  onUndo,
  onRemove,
  onExportPng,
  onExportCsv,
}: {
  title: string;
  state: "empty" | "ready" | "broken";
  render?: Render;
  ttlSeconds: number;
  canUndo: boolean;
  busy: boolean;
  onSelect: () => void;
  onMoveIntent: () => void;
  onRefresh: () => void;
  onUndo: () => void;
  onRemove: () => void;
  /** Absent when there is nothing to export yet, so the menu never offers
   *  an action that would do nothing. */
  onExportPng?: () => void;
  onExportCsv?: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const stale = ageMinutes(render?.fetched_at) > ttlSeconds / 60;
  const unplottable = state === "broken" && render?.error_reason === "unplottable";
  const hasValidResult = state === "ready" || unplottable;

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setMenuOpen(false);
      menuButtonRef.current?.focus();
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [menuOpen]);

  return (
    <div className="card-head">
      <div className="card-title-row">
        <div
          className="card-drag-zone drag-handle"
          title="Drag to move"
          aria-label="Drag to move card"
          onPointerDown={onMoveIntent}
          onClick={onSelect}
        >
          <span className="drag-grip" aria-hidden="true" />
          {state !== "ready" && (
            <span className={`card-state ${state}`}>{unplottable ? "too dense" : state}</span>
          )}
          <h2 className="card-title" title={title}>{title || "Untitled"}</h2>
        </div>
        {hasValidResult && canUndo && (
          <button
            className="icon-button"
            type="button"
            onClick={onUndo}
            disabled={busy}
            title="Undo the previous edit"
            aria-label="Undo the previous edit"
          >
            <UndoIcon />
          </button>
        )}
        {hasValidResult && (
          <button
            className="icon-button"
            type="button"
            onClick={onRefresh}
            disabled={busy}
            title="Refresh data"
            aria-label="Refresh data"
          >
            <RefreshIcon busy={busy} />
          </button>
        )}
        <div className="card-menu" ref={menuRef}>
          <button
            className="icon-button"
            type="button"
            ref={menuButtonRef}
            aria-label="Card actions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((value) => !value)}
          >
            <MenuIcon />
          </button>
          {menuOpen && (
            <div className="card-menu-popover" role="menu">
              {onExportPng && (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onExportPng();
                  }}
                >
                  Export chart as PNG
                </button>
              )}
              {onExportCsv && (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    onExportCsv();
                  }}
                >
                  Export rows as CSV
                </button>
              )}
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  onRemove();
                }}
              >
                Remove card
              </button>
            </div>
          )}
        </div>
      </div>

      {hasValidResult && render?.restatement && (
        <div className="trust-row">
          <p className="restatement" title={render.restatement} aria-label={render.restatement}>
            {render.restatement}
          </p>
          <div className="facts" aria-label="Data freshness">
            <span>{(render.row_count ?? 0).toLocaleString()} {render.row_count === 1 ? "row" : "rows"}</span>
            {render.data_max_ts && <span>through {render.data_max_ts.slice(0, 10)}</span>}
            <span className={stale ? "stale" : ""}>
              {clockOf(render.fetched_at)}{render.from_cache ? " · cached" : ""}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
