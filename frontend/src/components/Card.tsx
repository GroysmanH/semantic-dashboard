import { useState } from "react";
import type { Card as CardT } from "../api/client";
import { api } from "../api/client";
import CardHeader from "./CardHeader";
import EmptyCard from "./EmptyCard";
import SqlPanel from "./SqlPanel";
import VegaChart from "./VegaChart";

export default function Card({
  card,
  examples,
  onChanged,
}: {
  card: CardT;
  examples: string[];
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [askNote, setAskNote] = useState<string | null>(null);
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
            busy={busy}
            note={askNote}
            onAsk={async (question, hard) => {
              setBusy(true);
              setAskNote(null);
              try {
                const result = await api.ask(question, card.id, hard);
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
                rows={render.rows as Record<string, unknown>[]}
              />
            </div>
            {render.compiled_sql && <SqlPanel sql={render.compiled_sql} />}
          </>
        )}
      </div>
    </div>
  );
}
