import { useEffect, useRef, useState } from "react";
import type { SchemaInfo } from "../api/client";

/**
 * Which schema this dashboard asks its questions of, and what is in it.
 *
 * Two jobs in one control, deliberately. Choosing is the setting; the table
 * list underneath is how somebody finds out what choosing would get them.
 * A picker that offered five schema names and no way to see inside them
 * would be asking people to guess.
 *
 * The list is the warehouse catalogue, not the semantic layer, so it shows
 * tables this app cannot answer from. That is the honest shape of the
 * thing: the gap between what the database holds and what has been
 * described is real, and hiding it would make a half-modelled schema look
 * finished.
 */
export default function SchemaPicker({
  schemas,
  current,
  busy,
  onChoose,
}: {
  schemas: SchemaInfo[];
  current: string;
  busy: boolean;
  onChoose: (schema: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [showing, setShowing] = useState(current);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => setShowing(current), [current]);

  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  const shown = schemas.find((s) => s.schema === showing);

  return (
    <div className="schema-picker" ref={box}>
      <button
        type="button"
        className="schema-trigger"
        aria-expanded={open}
        aria-haspopup="true"
        disabled={busy}
        onClick={() => setOpen((was) => !was)}
      >
        <span className="eyebrow">Schema</span>
        <span className="schema-name">{current}</span>
        <svg viewBox="0 0 10 6" width="10" height="6" aria-hidden="true">
          <path d="M1 1l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      </button>

      {open && (
        <div className="schema-panel" role="dialog" aria-label="Choose a schema">
          <ul className="schema-list">
            {schemas.map((s) => (
              <li key={s.schema}>
                <button
                  type="button"
                  className={s.schema === showing ? "on" : undefined}
                  aria-current={s.schema === current}
                  onPointerEnter={() => setShowing(s.schema)}
                  onFocus={() => setShowing(s.schema)}
                  onClick={() => setShowing(s.schema)}
                >
                  <span>{s.schema}</span>
                  <span className="schema-count">
                    {s.answerable === 0
                      ? "nothing modelled"
                      : `${s.answerable} answerable`}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          {shown && (
            <div className="schema-tables">
              <p className="eyebrow">
                {shown.schema} · {shown.tables.length}{" "}
                {shown.tables.length === 1 ? "table" : "tables"}
              </p>
              <ul>
                {shown.tables.map((t) => (
                  <li key={t.table} className={t.entity ? "answerable" : "inert"}>
                    <span className="tick" aria-hidden="true">
                      {t.entity ? "✓" : "·"}
                    </span>
                    <span className="table-name">{t.table}</span>
                    <span className="table-note">
                      {t.entity
                        ? t.unverified.length
                          ? `${t.label} — unverified: ${t.unverified.join(", ")}`
                          : t.label
                        : t.joined_only
                          ? "joined into another entity, not queried directly"
                          : "not modelled — nobody has defined what its columns mean"}
                    </span>
                  </li>
                ))}
              </ul>

              {shown.schema !== current && (
                <button
                  type="button"
                  className="primary"
                  disabled={busy || shown.answerable === 0}
                  onClick={() => {
                    onChoose(shown.schema);
                    setOpen(false);
                  }}
                >
                  {shown.answerable === 0
                    ? "Nothing to ask here"
                    : `Ask ${shown.schema} on this dashboard`}
                </button>
              )}
              {/* Said before the click, not after. Cards already on the
                  board keep working either way, and somebody deciding
                  deserves to know that rather than discover it. */}
              {shown.schema !== current && shown.answerable > 0 && (
                <p className="schema-caveat">
                  Cards already here keep working. New questions will be
                  about {shown.schema}.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
