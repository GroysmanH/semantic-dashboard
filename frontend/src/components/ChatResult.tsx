import type { TransientResultView } from "../api/types.gen";
import SqlPanel from "./SqlPanel";
import VegaChart from "./VegaChart";

/** A query the chat ran to answer a question, shown with the same trust
 *  surface a card carries: the restatement, the row count, how fresh it is,
 *  and the compiled SQL underneath.
 *
 *  It is not a card and never becomes one by accident. Nothing here is
 *  persisted to a board unless someone asks for it.
 */
export default function ChatResult({
  result,
  sqlOpen,
  onSqlToggle,
  onRerun,
  busy,
}: {
  result: TransientResultView;
  sqlOpen: boolean;
  onSqlToggle: () => void;
  onRerun: () => void;
  busy: boolean;
}) {
  const rows = (result.rows ?? []) as Record<string, unknown>[];

  return (
    <div className="chat-result">
      <p className="restatement">{result.restatement}</p>
      <div className="facts">
        <span>
          {result.row_count} {result.row_count === 1 ? "row" : "rows"}
        </span>
        {result.data_max_ts && (
          <span>
            data through <b>{String(result.data_max_ts).slice(0, 10)}</b>
          </span>
        )}
      </div>

      {result.expired ? (
        // Never re-run on its own. Reopening a conversation must not go
        // back to the warehouse for numbers nobody asked for again.
        <div className="notice hint">
          <p>This result has expired.</p>
          <button type="button" onClick={onRerun} disabled={busy}>
            {busy ? "Running…" : "Run again"}
          </button>
        </div>
      ) : (
        <>
          {result.vega_spec && rows.length > 0 && (
            <div className="chat-chart">
              <VegaChart
                spec={result.vega_spec as Record<string, unknown>}
                rows={rows}
              />
            </div>
          )}
          {result.compiled_sql && (
            <SqlPanel
              sql={result.compiled_sql}
              open={sqlOpen}
              onToggle={onSqlToggle}
              // The drawer scrolls; nothing here reserves grid rows the
              // way a card does, so the measured height is not needed.
              onHeightChange={() => {}}
            />
          )}
        </>
      )}
    </div>
  );
}
