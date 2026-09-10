import { KAZAKHSTAN } from "../assets/kazakhstan";

export type ChartBounds = { width: number; height: number };
export type ChartConfig = Record<string, unknown>;

/** Identity for one exact rendered chart, including every input that can
 * change its pixels while the raw Vega-Lite spec remains unchanged. */
export function chartGeneration(
  spec: Record<string, unknown>,
  rows: Record<string, unknown>[],
  bounds: ChartBounds,
  config: ChartConfig,
): string {
  return JSON.stringify([spec, rows, bounds, config]);
}

function token(styles: CSSStyleDeclaration, name: string, fallback: string): string {
  return styles.getPropertyValue(name).trim() || fallback;
}

/** Read the application's fixed light palette once for either a visible or
 * an off-screen chart. The prepared spec below remains a pure transformation. */
export function chartConfig(
  styles: CSSStyleDeclaration = getComputedStyle(document.documentElement),
): ChartConfig {
  const ink = token(styles, "--ink", "#0d1f35");
  const soft = token(styles, "--ink-soft", "#51637a");
  const faint = token(styles, "--ink-faint", "#5a6c80");
  const rule = token(styles, "--rule", "#c9d5e2");
  const ruleSoft = token(styles, "--rule-soft", "#e2eaf2");
  const accent = token(styles, "--accent", "#00539b");
  const signal = token(styles, "--signal", "#9c5c00");

  return {
    background: "transparent",
    font: "system-ui, -apple-system, Segoe UI, sans-serif",
    axis: {
      labelFontSize: 10,
      titleFontSize: 10,
      titleFontWeight: 500,
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
      orient: "top",
      direction: "horizontal",
      labelFontSize: 10,
      titleFontSize: 10,
      titleFontWeight: 500,
      labelColor: soft,
      titleColor: faint,
      symbolSize: 70,
      symbolStrokeWidth: 2,
      labelLimit: 150,
      offset: 8,
    },
    mark: { color: accent },
    line: { strokeWidth: 2.2 },
    area: { opacity: 0.18 },
    bar: { cornerRadiusEnd: 2 },
    point: { filled: true },
    rule: { stroke: faint, strokeWidth: 1 },
    geoshape: { fill: ruleSoft, stroke: rule },
    view: { stroke: null },
    range: {
      category: [
        token(styles, "--chart-blue", "#00539b"),
        token(styles, "--chart-amber", "#c08427"),
        token(styles, "--chart-cyan", "#00a0df"),
        token(styles, "--chart-plum", "#8e5572"),
        "#2f7a6b",
        "#8a5a2b",
        "#4a6fa5",
        "#7a4f8b",
        "#3b7f7a",
        "#9a4f4f",
      ],
      heatmap: { scheme: "blues" },
      diverging: [signal, "#e8d9c0", accent],
    },
    text: { color: ink },
  };
}

export function chartMetadata(spec: Record<string, unknown>) {
  return (spec.usermeta ?? {}) as { presentation?: string; idealHeight?: number };
}

export function foldedCount(spec: Record<string, unknown>): number | null {
  const transforms = spec.transform as Array<Record<string, unknown>> | undefined;
  for (const transform of transforms ?? []) {
    if (Array.isArray(transform.fold)) return Math.max(1, transform.fold.length);
  }
  return null;
}

export function distinctCount(rows: Record<string, unknown>[], field: unknown): number {
  if (typeof field !== "string") return 1;
  return Math.max(1, new Set(rows.map((row) => row[field])).size);
}

export function kpiGrid(spec: Record<string, unknown>, bounds: ChartBounds) {
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
  const presentation = chartMetadata(spec).presentation;

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
    const idealHeight = chartMetadata(spec).idealHeight ?? height;
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

/** Purely prepare a complete Vega-Lite spec for any renderer. Named datasets
 * are replaced without disturbing geometry datasets or format metadata. */
export function prepareChartSpec(
  spec: Record<string, unknown>,
  rows: Record<string, unknown>[],
  bounds: ChartBounds,
  config: ChartConfig,
): Record<string, unknown> {
  if (!rows.length) {
    return {
      width: Math.max(1, bounds.width - 8),
      height: Math.max(1, bounds.height - 8),
      data: { values: [{ message: "No rows matched" }] },
      mark: { type: "text", align: "center", baseline: "middle" },
      encoding: { text: { field: "message", type: "nominal" } },
      autosize: { type: "fit", contains: "padding", resize: true },
      config,
    };
  }
  const datasets = { table: rows, outline: KAZAKHSTAN };
  const fill = (node: unknown): unknown => {
    if (Array.isArray(node)) return node.map(fill);
    if (node && typeof node === "object") {
      const obj = node as Record<string, unknown>;
      const name = (obj.data as { name?: string } | undefined)?.name;
      const filled = Object.fromEntries(
        Object.entries(obj).map(([key, value]) => [key, key === "data" ? value : fill(value)]),
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

  return {
    ...sizeSpec(fill(spec) as Record<string, unknown>, rows, bounds),
    config,
  };
}
