import { useCallback, useEffect, useRef, useState } from "react";
import type { Card as CardT, Provider } from "../api/client";
import { api } from "../api/client";
import AskBar from "./AskBar";
import CardHeader from "./CardHeader";
import EmptyCard from "./EmptyCard";
import Exchange, { replyPrompt, type ExchangeNote } from "./Exchange";
import SqlPanel from "./SqlPanel";
import VegaChart from "./VegaChart";
import { csvBlob } from "../export/csv";
import { prefs } from "../state/preferences";
import {
  chartConfig,
  snapshotChart,
  type ChartSnapshot,
  type MountedChartView,
} from "../export/chart";
import { download, downloadBlob, safeName, type VegaView } from "../export/png";

type PanelKind = "edit" | "sql";
type LiveChartView = { source: string; mounted: MountedChartView };

function failure(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function savedExchange(card: CardT): ExchangeNote | null {
  const pending = card.pending_clarification;
  if (!pending?.question) return null;
  return {
    kind: pending.kind === "refused" ? "refused" : "clarify",
    text: pending.question,
  };
}

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
  editing,
  sqlOpen,
  resizing,
  onSelect,
  onMoveIntent,
  onSqlToggle,
  onTransientHeight,
  onDuplicate,
  onChanged,
  onView,
  onSnapshot,
}: {
  card: CardT;
  examples: string[];
  provider: Provider;
  providers: Provider[];
  strongAvailable: boolean;
  onProviderChange: (p: Provider) => void;
  selected: boolean;
  editing?: boolean;
  sqlOpen: boolean;
  resizing: boolean;
  onSelect: () => void;
  onMoveIntent: () => void;
  onSqlToggle: () => void;
  onTransientHeight: (cardId: string, kind: PanelKind, height: number) => void;
  onDuplicate: (cardId: string) => Promise<void>;
  onChanged: () => void;
  /** Lets the board composite every chart into one image. */
  onView?: (view: VegaView | null) => void;
  /** Registers a fresh snapshot path even before react-vega has mounted. */
  onSnapshot?: (snapshot: ChartSnapshot | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [duplicating, setDuplicating] = useState(false);
  const [exchange, setExchange] = useState<ExchangeNote | null>(() => savedExchange(card));
  const [notice, setNotice] = useState<string | null>(null);
  const [changed, setChanged] = useState<string[]>([]);
  const editRef = useRef<HTMLDivElement>(null);
  const chartSlotRef = useRef<HTMLDivElement>(null);
  const render = card.render;
  const state = render?.state ?? card.state;
  const spec = render?.vega_spec as Record<string, unknown> | null | undefined;
  const chartRows = (render?.chart_rows ?? render?.rows ?? []) as Record<string, unknown>[];
  const chartVisible = state === "ready" && Boolean(spec);
  const chartSource = JSON.stringify([spec, chartRows]);
  // The mounted view additionally carries its exact render generation;
  // this source key retires it synchronously when refreshed rows arrive.
  const [liveView, setLiveView] = useState<LiveChartView | null>(null);
  const rawViewSourceRef = useRef<string | null>(null);
  const onViewRef = useRef(onView);
  onViewRef.current = onView;
  const mountedView = liveView && chartVisible && liveView.source === chartSource
    ? liveView.mounted
    : null;
  const open = exchange;
  const edit = open
    ? replyPrompt(open)
    : { placeholder: "Change this chart…", label: "Edit" };
  // What the note actually says: what was asked for, and what was drawn
  // instead. Dismissal is remembered against this rather than the card, so
  // a later, different override announces itself rather than inheriting a
  // decision made about a different sentence.
  const noteSignature = `${card.chart_hint ?? ""}>${render?.chart_type ?? ""}`;
  const [noteHidden, setNoteHidden] = useState(false);

  useEffect(() => {
    setNoteHidden(prefs.dismissedChartNotice(card.id) === noteSignature);
  }, [card.id, noteSignature]);

  const hideNote = useCallback(() => {
    prefs.dismissChartNotice(card.id, noteSignature);
    setNoteHidden(true);
  }, [card.id, noteSignature]);

  const unplottable = state === "broken" && render?.error_reason === "unplottable";
  const editable = state === "ready" || unplottable;
  // Evidence navigation can reveal and select a card without implying an
  // edit. Callers that do not distinguish the two keep the established
  // direct-selection behavior.
  const editOpen = (editing ?? selected) && editable;

  // The unfinished exchange is card data, not ephemeral component data.
  // Rehydrate it when the board reloads so a refresh cannot turn a useful
  // reply box back into an unrelated fresh-question composer.
  useEffect(() => {
    setExchange(savedExchange(card));
  }, [
    card.id,
    card.pending_clarification?.kind,
    card.pending_clarification?.question,
  ]);

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
    const rawIsCurrent = chartVisible
      && rawViewSourceRef.current === chartSource;
    if (rawViewSourceRef.current && !rawIsCurrent) {
      rawViewSourceRef.current = null;
      onViewRef.current?.(null);
    }
    if (liveView && (!chartVisible || liveView.source !== chartSource)) {
      setLiveView(null);
    }
  }, [card.id, chartSource, chartVisible, liveView]);

  useEffect(() => () => onViewRef.current?.(null), []);

  const receiveView = useCallback((nextView: VegaView | null) => {
    rawViewSourceRef.current = nextView ? chartSource : null;
    if (!nextView) setLiveView(null);
    onViewRef.current?.(nextView);
  }, [chartSource]);

  const receiveMountedView = useCallback((nextView: MountedChartView | null) => {
    setLiveView(nextView ? { source: chartSource, mounted: nextView } : null);
  }, [chartSource]);

  const refresh = async () => {
    setBusy(true);
    try {
      await api.refreshCard(card.id);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const snapshot = useCallback(async () => {
    if (!spec) throw new Error("This chart is not ready to export.");
    const measured = chartSlotRef.current?.getBoundingClientRect();
    const bounds = measured && measured.width > 0 && measured.height > 0
      ? { width: Math.floor(measured.width), height: Math.floor(measured.height) }
      : {
          width: Math.max(320, card.layout.w * 110 - 10),
          height: Math.max(180, card.layout.h * 34 - 10),
        };
    return snapshotChart({
      spec,
      rows: chartRows,
      bounds,
      config: chartConfig(),
      mounted: mountedView,
    });
  }, [card.layout.h, card.layout.w, chartRows, mountedView, spec]);

  useEffect(() => {
    onSnapshot?.(chartVisible ? snapshot : null);
    return () => onSnapshot?.(null);
  }, [chartVisible, onSnapshot, snapshot]);

  const exportPng = async () => {
    setNotice(null);
    try {
      download(await snapshot(), safeName(card.title, "png"));
    } catch (error) {
      // Say so on the card. A download that silently does nothing reads as
      // a broken button rather than a failure.
      setNotice(failure(error));
    }
  };

  const exportCsv = () => {
    // The full result set, not chart_rows: chart_rows may be collapsed into
    // an "Other" bucket for legibility, and a CSV is for the numbers.
    const rows = (render?.rows ?? []) as Record<string, unknown>[];
    downloadBlob(csvBlob(rows), safeName(card.title, "csv"));
  };

  const reportExportError = useCallback((error: unknown) => {
    setNotice(failure(error));
  }, []);

  /** One path for every question this card asks, empty or ready.
   *
   *  `reply` is what tells the server whether to read this as an answer to
   *  what the card last said or as a fresh request, and it is always stated
   *  rather than inferred -- an exchange left open on the server would
   *  otherwise colour a question that had nothing to do with it. */
  const ask = async (question: string, hard: boolean, reply: boolean) => {
    setBusy(true);
    setNotice(null);
    // Keep the exchange visible while its reply is in flight. If the
    // provider fails, the person can retry without reloading the card or
    // losing the question they were answering.
    if (!reply) setExchange(null);
    try {
      // The same provider the rest of the session uses. An edit that
      // silently switched model would make two cards incomparable.
      const result = await api.ask(question, card.id, hard, provider, reply);
      if (result.state === "clarify" || result.state === "refused") {
        if (result.replyable === false) {
          setNotice(result.message);
        } else {
          setExchange({ kind: result.state, text: result.message });
          // The server persisted the exchange. Refresh the board copy so the
          // same reply prompt survives a later rerender or page reload.
          onChanged();
        }
      } else {
        setExchange(null);
        setChanged(result.changed ?? []);
        onChanged();
      }
    } catch (error) {
      setNotice(failure(error));
    } finally {
      setBusy(false);
    }
  };

  const undo = async () => {
    setBusy(true);
    setChanged([]);
    setNotice(null);
    try {
      await api.undoCard(card.id);
      setExchange(null);
      onChanged();
    } catch (error) {
      setNotice(failure(error));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    await api.deleteCard(card.id);
    onChanged();
  };

  const duplicate = async () => {
    setBusy(true);
    setDuplicating(true);
    setNotice(null);
    try {
      await onDuplicate(card.id);
    } catch (error) {
      setNotice(failure(error));
    } finally {
      setDuplicating(false);
      setBusy(false);
    }
  };

  const dismissExchange = async () => {
    setBusy(true);
    setNotice(null);
    try {
      await api.dismissCardExchange(card.id);
      setExchange(null);
      onChanged();
    } catch (error) {
      // Keep the exchange replyable when persistence fails. Losing the UI
      // optimistically would make a reload bring back something that looked
      // dismissed.
      setNotice(failure(error));
    } finally {
      setBusy(false);
    }
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
      aria-busy={busy}
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
        onExportPng={chartVisible ? exportPng : undefined}
        onExportCsv={state === "ready" && render ? exportCsv : undefined}
        onExportError={reportExportError}
        title={card.title || "Untitled"}
        state={state}
        render={render}
        ttlSeconds={card.ttl_seconds}
        canUndo={card.can_undo}
        busy={busy}
        duplicating={duplicating}
        onSelect={onSelect}
        onMoveIntent={onMoveIntent}
        onRefresh={refresh}
        onUndo={undo}
        onDuplicate={state === "ready" ? duplicate : undefined}
        onRemove={remove}
      />

      <div className="card-body">
        {notice && <div className="notice hint">{notice}</div>}
        {exchange && !editOpen && state !== "empty" && (
          <div className="notice hint">{exchange.text}</div>
        )}
        {editOpen && (
          <div className="refine transient-panel" ref={editRef} data-card-control>
            {open && (
              <Exchange note={open} busy={busy} onDismiss={dismissExchange} />
            )}
            <AskBar
              placeholder={edit.placeholder}
              submitLabel={edit.label}
              busy={busy}
              onSubmit={(question) => ask(question, false, open !== null)}
            />
            {changed.length > 0 && (
              <div className="notice hint">Changed: {changed.join(", ")}.</div>
            )}
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
            exchange={exchange}
            onDismissNote={dismissExchange}
            onAsk={ask}
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
            {render?.hint_rejected && !noteHidden && (
              <div className="notice hint chart-notice">
                <span>
                  Requested chart wasn’t useful for this result; showing{" "}
                  {fallbackDescription(render?.chart_type)} instead.
                </span>
                <button
                  type="button"
                  className="notice-dismiss"
                  aria-label="Hide this note"
                  title="Hide this note"
                  data-card-control
                  onClick={hideNote}
                >
                  ×
                </button>
              </div>
            )}
            <div className="chart-slot" data-card-control ref={chartSlotRef}>
              <VegaChart
                spec={spec}
                rows={chartRows}
                resizing={resizing}
                onView={receiveView}
                onMountedView={receiveMountedView}
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
