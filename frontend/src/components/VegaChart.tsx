import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { VegaLite } from "react-vega";
import { KAZAKHSTAN } from "../assets/kazakhstan";
import type { VegaView } from "../export/png";

function token(styles: CSSStyleDeclaration, name: string, fallback: string): string {
  return styles.getPropertyValue(name).trim() || fallback;
}

function useChartConfig() {
  return useMemo(() => {
    const styles = getComputedStyle(document.documentElement);
    const ink = token(styles, "--ink", "#16201c");
    const soft = token(styles, "--ink-soft", "#5a6660");
    const faint = token(styles, "--ink-faint", "#8b968f");
    const rule = token(styles, "--rule", "#cdd6ce");
    const ruleSoft = token(styles, "--rule-soft", "#e3e9e3");
    const accent = token(styles, "--accent", "#1f6f63");
    const signal = token(styles, "--signal", "#b4611a");

    return {
      background: "transparent",
      font: "system-ui, -apple-system, Segoe UI, sans-serif",
      axis: {
        labelFontSize: 10,
        titleFontSize: 10,
        titleFontWeight: 500 as const,
        labelColor: faint,
        titleColor: soft,
        domainColor: rule,
        tickColor: rule,
        gridColor: ruleSoft,
        gridOpacity: 0.72,
        labelPadding: 5,
        titlePadding: 8,
      },
      axisX: { grid: false },
      axisY: { grid: true, domain: false },
      legend: {
        orient: "top" as const,
        direction: "horizontal" as const,
        labelFontSize: 10,
        titleFontSize: 10,
        titleFontWeight: 500 as const,
        labelColor: soft,
        titleColor: faint,
        symbolSize: 70,
        symbolStrokeWidth: 2,
        labelLimit: 150,
        offset: 8,
      },
      line: { strokeWidth: 2.2 },
      area: { opacity: 0.18 },
      bar: { cornerRadiusEnd: 2 },
      point: { filled: true },
      rule: { stroke: faint, strokeWidth: 1 },
      geoshape: { fill: ruleSoft, stroke: rule },
      view: { stroke: null },
      range: {
        category: [
          accent,
          signal,
          token(styles, "--chart-blue", "#4f7fae"),
          token(styles, "--chart-purple", "#7463a5"),
          token(styles, "--chart-olive", "#87923d"),
          token(styles, "--chart-rose", "#a75468"),
          "#2f6f9f",
          "#9a5d22",
          "#567a3a",
          "#8b4f83",
          "#3b7f7a",
          "#9a4f4f",
        ],
        heatmap: { scheme: "teals" },
      },
      text: { color: ink },
    };
  }, []);
}

type ChartBounds = { width: number; height: number };

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

function metadata(spec: Record<string, unknown>) {
  return (spec.usermeta ?? {}) as { presentation?: string; idealHeight?: number };
}

function foldedCount(spec: Record<string, unknown>): number | null {
  const transforms = spec.transform as Array<Record<string, unknown>> | undefined;
  for (const transform of transforms ?? []) {
    if (Array.isArray(transform.fold)) return Math.max(1, transform.fold.length);
  }
  return null;
}

function distinctCount(rows: Record<string, unknown>[], field: unknown): number {
  if (typeof field !== "string") return 1;
  return Math.max(1, new Set(rows.map((row) => row[field])).size);
}

function kpiGrid(spec: Record<string, unknown>, bounds: ChartBounds) {
  const children = spec.hconcat as Array<Record<string, unknown>> | undefined;
  if (!children?.length) return null;
  const width = Math.max(1, bounds.width - 8);
  const spacing = 16;
  const minimumCellWidth = 150;
  const columns = Math.max(1, Math.min(
    children.length,
    Math.floor((width + spacing) / (minimumCellWidth + spacing)),
  ));
  const availableCellWidth = Math.floor(
    (width - spacing * Math.max(0, columns - 1)) / columns,
  );
  return {
    children,
    columns,
    rows: Math.ceil(children.length / columns),
    spacing,
    cellWidth: Math.max(1, Math.min(220, availableCellWidth)),
    cellHeight: 86,
  };
}

function sizeSpec(
  spec: Record<string, unknown>,
  rows: Record<string, unknown>[],
  bounds: ChartBounds,
): Record<string, unknown> {
  const width = Math.max(1, bounds.width - 8);
  const height = Math.max(1, bounds.height - 8);
  const presentation = metadata(spec).presentation;

  if (presentation === "kpi") {
    const grid = kpiGrid(spec, bounds);
    if (grid) {
      const { hconcat: _hconcat, ...rest } = spec;
      return {
        ...rest,
        concat: grid.children.map((child) => ({
          ...child,
          width: grid.cellWidth,
          height: grid.cellHeight,
        })),
        columns: grid.columns,
        spacing: grid.spacing,
        center: true,
      };
    }
    return {
      ...spec,
      width: Math.min(width, 520),
      height: 116,
      autosize: { type: "fit", contains: "padding", resize: true },
    };
  }

  if ("facet" in spec) {
    const facet = spec.facet as Record<string, unknown>;
    const inner = spec.spec as Record<string, unknown>;
    const row = facet.row as Record<string, unknown> | undefined;
    if (row) {
      const panels = foldedCount(spec) ?? distinctCount(rows, row.field);
      const panelHeight = Math.max(90, Math.min(140, Math.floor(
        (height - Math.max(0, panels - 1) * 24) / panels,
      )));
      return {
        ...spec,
        spec: {
          ...inner,
          width: Math.max(1, width - 108),
          height: panelHeight,
        },
      };
    }

    const panels = distinctCount(rows, facet.field);
    const columns = Math.min(
      panels,
      typeof facet.columns === "number" ? facet.columns : 3,
      Math.max(1, Math.floor(width / 190)),
    );
    const panelRows = Math.ceil(panels / columns);
    const cellWidth = Math.floor((width - (columns - 1) * 18) / columns);
    return {
      ...spec,
      spec: {
        ...inner,
        width: Math.max(1, cellWidth - 56),
        height: Math.max(100, Math.min(180, Math.floor(
          (height - (panelRows - 1) * 30) / panelRows,
        ))),
      },
    };
  }

  if (presentation === "ranked") {
    const idealHeight = metadata(spec).idealHeight ?? height;
    return {
      ...spec,
      width,
      height: Math.min(height, Math.max(84, idealHeight)),
      autosize: { type: "fit", contains: "padding", resize: true },
    };
  }

  return {
    ...spec,
    width,
    height,
    autosize: { type: "fit", contains: "padding", resize: true },
  };
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
  if (metadata(spec).presentation !== "kpi") return false;
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
}: {
  spec: Record<string, unknown>;
  rows: Record<string, unknown>[];
  resizing?: boolean;
  /** Hands the live Vega view up so the card can rasterise it. Export is
   *  ours rather than vega-embed's, so `actions` stays off. */
  onView?: (view: VegaView | null) => void;
}) {
  const config = useChartConfig();
  const { ref, bounds, liveBounds } = useChartBounds(resizing);
  const onViewRef = useRef(onView);
  onViewRef.current = onView;
  // react-vega recreates and finalizes its View whenever onNewView changes.
  // Keep this adapter stable even when delivering the view updates parent
  // state; the ref still forwards to the latest owner callback.
  const handleNewView = useCallback((view: unknown) => {
    onViewRef.current?.(view as VegaView);
  }, []);
  const presentation = metadata(spec).presentation;
  const compact = presentation === "kpi" || presentation === "ranked";
  const scrollable = needsVerticalScroll(spec, rows, bounds) || needsKpiScroll(spec, bounds);
  // Rows are still inlined rather than bound through Vega's named-data
  // handoff, which silently yields an empty view (it reports an infinite
  // extent rather than an error). What changed is the target: replacing
  // the whole `data` key erased a map's geometry layer, so the row values
  // now go to the dataset that asked for them and any other dataset -- the
  // country outline -- is left alone.
  const full = useMemo(() => {
    const datasets = { table: rows, outline: KAZAKHSTAN };
    const fill = (node: unknown): unknown => {
      if (Array.isArray(node)) return node.map(fill);
      if (node && typeof node === "object") {
        const obj = node as Record<string, unknown>;
        const name = (obj.data as { name?: string } | undefined)?.name;
        const filled = Object.fromEntries(
          Object.entries(obj).map(([k, v]) => [k, k === "data" ? v : fill(v)]),
        );
        if (name && name in datasets) {
          const format = (obj.data as { format?: unknown }).format;
          filled.data = {
            values: datasets[name as keyof typeof datasets],
            ...(format ? { format } : {}),
          };
        }
        return filled;
      }
      return node;
    };

    const filled = fill(spec) as Record<string, unknown>;

    // Composite views cannot use Vega-Lite's `container` size. Calculate
    // their panel geometry from the actual card instead, and use the same
    // numerical sizing for unit views so sparse charts can stay compact.
    return { ...sizeSpec(filled, rows, bounds), config };
  }, [bounds, config, spec, rows]);

  if (!rows.length) {
    return <p className="eyebrow">No rows matched.</p>;
  }

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
