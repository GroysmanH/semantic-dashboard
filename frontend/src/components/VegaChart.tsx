import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { VegaLite } from "react-vega";
import type { VegaView } from "../export/png";
import type { MountedChartView } from "../export/chart";
import {
  chartConfig,
  chartGeneration,
  chartMetadata,
  distinctCount,
  foldedCount,
  kpiGrid,
  prepareChartSpec,
  type ChartBounds,
} from "../charts/prepare";

export { prepareChartSpec } from "../charts/prepare";

function useChartBounds(resizing: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const [liveBounds, setLiveBounds] = useState<ChartBounds>({ width: 640, height: 240 });
  const [bounds, setBounds] = useState<ChartBounds>({ width: 640, height: 240 });
  const resizingRef = useRef(resizing);
  resizingRef.current = resizing;

  useEffect(() => {
    const shell = ref.current;
    if (!shell) return;
    const measure = (box: DOMRectReadOnly) => {
      const width = Math.floor(box.width);
      const height = Math.floor(box.height);
      if (width <= 0 || height <= 0) return;
      const next = { width, height };
      setLiveBounds((current) => (
        current.width === width && current.height === height
          ? current
          : next
      ));
      if (!resizingRef.current) {
        setBounds((current) => (
          current.width === width && current.height === height ? current : next
        ));
      }
    };
    measure(shell.getBoundingClientRect());
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) measure(entry.contentRect);
    });
    observer.observe(shell);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!resizing) setBounds(liveBounds);
  }, [liveBounds, resizing]);

  return { ref, bounds, liveBounds };
}

function needsVerticalScroll(
  spec: Record<string, unknown>,
  rows: Record<string, unknown>[],
  bounds: ChartBounds,
): boolean {
  if (!("facet" in spec)) return false;
  const facet = spec.facet as Record<string, unknown>;
  const row = facet.row as Record<string, unknown> | undefined;
  const height = Math.max(1, bounds.height - 8);
  if (row) {
    const panels = foldedCount(spec) ?? distinctCount(rows, row.field);
    return panels * 90 + Math.max(0, panels - 1) * 24 > height;
  }

  const width = Math.max(1, bounds.width - 8);
  const panels = distinctCount(rows, facet.field);
  const columns = Math.min(
    panels,
    typeof facet.columns === "number" ? facet.columns : 3,
    Math.max(1, Math.floor(width / 190)),
  );
  const panelRows = Math.ceil(panels / columns);
  return panelRows * 100 + Math.max(0, panelRows - 1) * 30 > height;
}

function needsKpiScroll(spec: Record<string, unknown>, bounds: ChartBounds): boolean {
  if (chartMetadata(spec).presentation !== "kpi") return false;
  const grid = kpiGrid(spec, bounds);
  if (!grid) return false;
  const requiredHeight = grid.rows * grid.cellHeight
    + Math.max(0, grid.rows - 1) * grid.spacing;
  return requiredHeight > Math.max(1, bounds.height - 8);
}

export default function VegaChart({
  spec,
  rows,
  resizing = false,
  onView,
  onMountedView,
}: {
  spec: Record<string, unknown>;
  rows: Record<string, unknown>[];
  resizing?: boolean;
  /** Hands the live Vega view up so the card can rasterise it. Export is
   *  ours rather than vega-embed's, so `actions` stays off. */
  onView?: (view: VegaView | null) => void;
  onMountedView?: (view: MountedChartView | null) => void;
}) {
  const config = useMemo(() => chartConfig(), []);
  const { ref, bounds, liveBounds } = useChartBounds(resizing);
  const onViewRef = useRef(onView);
  onViewRef.current = onView;
  const onMountedViewRef = useRef(onMountedView);
  onMountedViewRef.current = onMountedView;
  const presentation = chartMetadata(spec).presentation;
  const compact = presentation === "kpi" || presentation === "ranked";
  const scrollable = needsVerticalScroll(spec, rows, bounds) || needsKpiScroll(spec, bounds);
  const full = useMemo(
    () => prepareChartSpec(spec, rows, bounds, config),
    [bounds, config, spec, rows],
  );
  const generation = useMemo(
    () => chartGeneration(spec, rows, bounds, config),
    [bounds, config, rows, spec],
  );
  const currentGeneration = useRef(generation);
  currentGeneration.current = generation;
  // A spec can be replaced while react-vega is still creating the previous
  // View. Give each prepared render its own adapter: late callbacks see that
  // their generation is no longer current and never reach the card.
  const handleNewView = useCallback((view: unknown) => {
    if (currentGeneration.current !== generation) return;
    onViewRef.current?.(view as VegaView);
    onMountedViewRef.current?.({ generation, view: view as VegaView });
  }, [generation]);

  useEffect(() => {
    onViewRef.current?.(null);
    onMountedViewRef.current?.(null);
    return () => {
      if (currentGeneration.current !== generation) return;
      onViewRef.current?.(null);
      onMountedViewRef.current?.(null);
    };
  }, [generation]);

  const scaleX = liveBounds.width / Math.max(1, bounds.width);
  const scaleY = liveBounds.height / Math.max(1, bounds.height);
  const previewStyle = resizing ? {
    width: bounds.width,
    height: bounds.height,
    transform: `scale(${scaleX}, ${scaleY})`,
    transformOrigin: "top left",
  } : undefined;

  return (
    <div
      className={`chart-shell${compact ? " compact" : ""}${scrollable ? " scrollable" : ""}${resizing ? " resizing-preview" : ""}`}
      ref={ref}
      style={{ minHeight: 140 }}
    >
      <div className="chart-render-frame" style={previewStyle}>
        <VegaLite
          spec={full as never}
          actions={false}
          onNewView={handleNewView}
          renderer="svg"
          style={{
            width: "100%",
            height: compact || scrollable ? "auto" : "100%",
          }}
        />
      </div>
    </div>
  );
}
