import { useCallback, useEffect, useRef, useState } from "react";
import type { Card as CardT, Provider } from "../api/client";
import { api } from "../api/client";
import AskBar from "./AskBar";
import CardHeader from "./CardHeader";
import EmptyCard from "./EmptyCard";
import SqlPanel from "./SqlPanel";
import VegaChart from "./VegaChart";
import { csvBlob } from "../export/csv";
import { chartPng, download, downloadBlob, safeName, type VegaView } from "../export/png";

type PanelKind = "edit" | "sql";
type LiveChartView = { spec: Record<string, unknown>; view: VegaView };

function fallbackDescription(chartType: string | null | undefined): string {
  if (chartType === "big_number") return "a KPI";
  if (!chartType) return "a more suitable chart";
  return `a ${chartType.replaceAll("_", " ")} chart`;
}

export default function Card({
  card,
  examples,
  provider,
  providers,
  strongAvailable,
  onProviderChange,
  selected,
  sqlOpen,
  resizing,
  onSelect,
  onMoveIntent,
  onSqlToggle,
  onTransientHeight,
  onChanged,
  onView,
}: {
  card: CardT;
  examples: string[];
  provider: Provider;
  providers: Provider[];
  strongAvailable: boolean;
  onProviderChange: (p: Provider) => void;
  selected: boolean;
  sqlOpen: boolean;
  resizing: boolean;
  onSelect: () => void;
  onMoveIntent: () => void;
  onSqlToggle: () => void;
  onTransientHeight: (cardId: string, kind: PanelKind, height: number) => void;
  onChanged: () => void;
  /** Lets the board composite every chart into one image. */
  onView?: (view: VegaView | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [askNote, setAskNote] = useState<string | null>(null);
  const [changed, setChanged] = useState<string[]>([]);
  const editRef = useRef<HTMLDivElement>(null);
  const render = card.render;
  const state = render?.state ?? card.state;
  const spec = render?.vega_spec as Record<string, unknown> | null | undefined;
  const chartRows = (render?.chart_rows ?? render?.rows ?? []) as Record<string, unknown>[];
  const chartVisible = state === "ready" && Boolean(spec) && chartRows.length > 0;
  // Tie a Vega view to the exact spec that created it. A ref alone can
  // silently keep exporting the previous chart after a card refresh.
  const [liveView, setLiveView] = useState<LiveChartView | null>(null);
  const onViewRef = useRef(onView);
  onViewRef.current = onView;
  const view = liveView && chartVisible && liveView.spec === spec ? liveView.view : null;
  const unplottable = state === "broken" && render?.error_reason === "unplottable";
  const editable = state === "ready" || unplottable;
  const editOpen = selected && editable;

  useEffect(() => {
    if (!editOpen || !editRef.current) {
      onTransientHeight(card.id, "edit", 0);
      return;
    }

    const panel = editRef.current;
    const measure = () => {
      const styles = getComputedStyle(panel);
      const margin = (Number.parseFloat(styles.marginTop) || 0)
        + (Number.parseFloat(styles.marginBottom) || 0);
      onTransientHeight(card.id, "edit", Math.ceil(panel.getBoundingClientRect().height + margin));
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(panel);
    return () => observer.disconnect();
  }, [card.id, editOpen, onTransientHeight]);

  useEffect(() => {
    if (!sqlOpen) onTransientHeight(card.id, "sql", 0);
  }, [card.id, onTransientHeight, sqlOpen]);

  useEffect(() => {
    if (!liveView || (chartVisible && liveView.spec === spec)) return;
    setLiveView(null);
    onViewRef.current?.(null);
  }, [card.id, chartVisible, liveView, spec]);

  useEffect(() => () => onViewRef.current?.(null), []);

  const receiveView = useCallback((nextView: VegaView | null) => {
    setLiveView(nextView && spec ? { spec, view: nextView } : null);
    onViewRef.current?.(nextView);
  }, [spec]);

  const refresh = async () => {
    setBusy(true);
    try {
      await api.refreshCard(card.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const exportPng = async () => {
    if (!view) return;
    setAskNote(null);
    try {
      download(await chartPng(view), safeName(card.title, "png"));
    } catch (error) {
      // Say so on the card. A download that silently does nothing reads as
      // a broken button rather than a failure.
      setAskNote(error instanceof Error ? error.message : String(error));
    }
  };

  const exportCsv = () => {
    // The full result set, not chart_rows: chart_rows may be collapsed into
    // an "Other" bucket for legibility, and a CSV is for the numbers.
    const rows = (render?.rows ?? []) as Record<string, unknown>[];
    downloadBlob(csvBlob(rows), safeName(card.title, "csv"));
  };

  const reportExportError = useCallback((error: unknown) => {
    setAskNote(error instanceof Error ? error.message : String(error));
  }, []);

  const refine = async (question: string) => {
    setBusy(true);
    setAskNote(null);
    try {
      // The same provider the rest of the session uses. An edit that
      // silently switched model would make two cards incomparable.
      const result = await api.ask(question, card.id, false, provider);
      if (result.state === "clarify" || result.state === "refused") {
        setAskNote(result.message);
      } else {
        setChanged(result.changed ?? []);
        onChanged();
      }
    } catch (error) {
      setAskNote(error instanceof Error ? error.message : String(error));
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
    } catch (error) {
      setAskNote(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    await api.deleteCard(card.id);
    onChanged();
  };

  const reportSqlHeight = useCallback(
    (height: number) => onTransientHeight(card.id, "sql", height),
    [card.id, onTransientHeight],
  );

  return (
    <article
      className={`card ${state} ${selected ? "selected" : ""}`}
      tabIndex={0}
      aria-label={`${card.title || "Untitled"} card`}
      aria-selected={selected}
      onClick={(event) => {
        const target = event.target as Element;
        if (target.closest("button, input, form, a, [data-card-control], .drag-handle")) return;
        onSelect();
      }}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget || (event.key !== "Enter" && event.key !== " ")) return;
        event.preventDefault();
        onSelect();
      }}
    >
      <CardHeader
        onExportPng={view ? exportPng : undefined}
        onExportCsv={render?.rows?.length ? exportCsv : undefined}
        onExportError={reportExportError}
        title={card.title || "Untitled"}
        state={state}
        render={render}
        ttlSeconds={card.ttl_seconds}
        canUndo={card.can_undo}
        busy={busy}
        onSelect={onSelect}
        onMoveIntent={onMoveIntent}
        onRefresh={refresh}
        onUndo={undo}
        onRemove={remove}
      />

      <div className="card-body">
        {askNote && !editOpen && state !== "empty" && (
          <div className="notice hint">{askNote}</div>
        )}
        {editOpen && (
          <div className="refine transient-panel" ref={editRef} data-card-control>
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

        {state === "empty" && (
          <EmptyCard
            examples={examples}
            provider={provider}
            providers={providers}
            strongAvailable={strongAvailable}
            onProviderChange={onProviderChange}
            busy={busy}
            note={askNote}
            onAsk={async (question, hard) => {
              setBusy(true);
              setAskNote(null);
              try {
                const result = await api.ask(question, card.id, hard, provider);
                if (result.state === "clarify" || result.state === "refused") {
                  setAskNote(result.message);
                } else {
                  onChanged();
                }
              } catch (error) {
                setAskNote(error instanceof Error ? error.message : String(error));
              } finally {
                setBusy(false);
              }
            }}
          />
        )}

        {state === "broken" && (
          <div className={`notice ${unplottable ? "hint" : "broken"}`}>
            <strong>
              {unplottable
                ? "This result is valid, but too dense to visualize."
                : "This card no longer matches the semantic layer."}
            </strong>
            <br />
            {render?.error}
          </div>
        )}

        {state === "ready" && spec && (
          <>
            {render?.hint_rejected && (
              <div className="notice hint chart-notice">
                Requested chart wasn’t useful for this result; showing {fallbackDescription(render?.chart_type)} instead.
              </div>
            )}
            <div className="chart-slot" data-card-control>
              <VegaChart
                spec={spec}
                rows={chartRows}
                resizing={resizing}
                onView={receiveView}
              />
            </div>
          </>
        )}

        {(state === "ready" || unplottable) && render?.compiled_sql && (
          <SqlPanel
            sql={render.compiled_sql}
            open={sqlOpen}
            onToggle={onSqlToggle}
            onHeightChange={reportSqlHeight}
          />
        )}
      </div>
    </article>
  );
}
