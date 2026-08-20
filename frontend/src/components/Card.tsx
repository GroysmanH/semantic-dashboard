import { useState } from "react";
import type { Card as CardT, Providers } from "../api/client";
import { api } from "../api/client";
import CardHeader from "./CardHeader";
import AskBar from "./AskBar";
import EmptyCard from "./EmptyCard";
import SqlPanel from "./SqlPanel";
import VegaChart from "./VegaChart";

export default function Card({
  card,
  examples,
  providers,
  onChanged,
}: {
  card: CardT;
  examples: string[];
  providers: Providers;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [askNote, setAskNote] = useState<string | null>(null);
  // Transient on purpose: it confirms what an edit just did, and a reload
  // is a new arrival at the card rather than a change to it.
  const [changed, setChanged] = useState<string[]>([]);
  const render = card.render;
  const state = render?.state ?? card.state;

  const refresh = async () => {
    setBusy(true);
    try {
      await api.refreshCard(card.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  /** An edit to the card that is already here. The question box does not
   *  offer provider or difficulty -- those belong to a new question, not a
   *  change to an existing answer. */
  const refine = async (question: string) => {
    setBusy(true);
    setAskNote(null);
    try {
      const result = await api.ask(question, card.id);
      if (result.state === "clarify" || result.state === "refused") {
        setAskNote(result.message);
      } else {
        setChanged(result.changed ?? []);
        onChanged();
      }
    } catch (e) {
      setAskNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const undo = async () => {
    setBusy(true);
    setChanged([]);
    setAskNote(null);
    try {
      await api.undoCard(card.id);
      onChanged();
    } catch (e) {
      setAskNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    await api.deleteCard(card.id);
    onChanged();
  };

  return (
    <div className={`card ${state}`}>
      <div className="card-head">
        <div className="card-title-row">
          <span className="eyebrow">{state}</span>
          <h2 className="card-title">{card.title || "Untitled"}</h2>
          <span className="drag-handle" title="Drag to move" aria-hidden="true" />
          {card.can_undo && (
            <button onClick={undo} disabled={busy} title="Put back the previous version">
              Undo
            </button>
          )}
          {state === "ready" && (
            <button onClick={refresh} disabled={busy} title="Fetch again now">
              {busy ? "…" : "Refresh"}
            </button>
          )}
          <button onClick={remove} title="Remove this card">
            Remove
          </button>
        </div>

        {render && state === "ready" && (
          <CardHeader render={render} ttlSeconds={card.ttl_seconds} />
        )}
      </div>

      <div className="card-body">
        {state === "empty" && (
          <EmptyCard
            examples={examples}
            providers={providers}
            busy={busy}
            note={askNote}
            onAsk={async (question, hard, provider) => {
              setBusy(true);
              setAskNote(null);
              try {
                const result = await api.ask(question, card.id, hard, provider);
                if (result.state === "clarify" || result.state === "refused") {
                  setAskNote(result.message);
                } else {
                  onChanged();
                }
              } catch (e) {
                setAskNote(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          />
        )}

        {state === "broken" && (
          <div className="notice broken">
            <strong>This card no longer matches the semantic layer.</strong>
            <br />
            {render?.error}
          </div>
        )}

        {state === "ready" && (
          <div className="refine">
            <AskBar
              placeholder="Change this chart…"
              submitLabel="Edit"
              busy={busy}
              onSubmit={refine}
            />
            {changed.length > 0 && (
              <div className="notice hint">Changed: {changed.join(", ")}.</div>
            )}
            {askNote && <div className="notice hint">{askNote}</div>}
          </div>
        )}

        {state === "ready" && render?.vega_spec && (
          <>
            {render.hint_rejected && (
              <div className="notice hint" style={{ marginBottom: "0.5rem" }}>
                That chart type does not fit this data, so it is drawn as a{" "}
                {render.chart_type}.
              </div>
            )}
            <div className="chart-slot">
              <VegaChart
                spec={render.vega_spec as Record<string, unknown>}
                /* chart_rows is set only when the builder changed what the
                   chart draws -- a pie's collapsed tail. `rows` stays the
                   untouched result set behind the SQL panel and row count,
                   and the restatement explains the difference. */
                rows={
                  (render.chart_rows ?? render.rows) as Record<string, unknown>[]
                }
              />
            </div>
            {render.compiled_sql && <SqlPanel sql={render.compiled_sql} />}
          </>
        )}
      </div>
    </div>
  );
}
