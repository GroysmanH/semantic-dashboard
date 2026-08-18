import { useMemo } from "react";
import { VegaLite } from "react-vega";

/** Chart styling is applied here, not in the spec the backend builds --
 *  the spec stays a statement of what is plotted, this is how it looks. */
const CONFIG = {
  background: "transparent",
  font: "ui-monospace, SF Mono, Menlo, monospace",
  axis: {
    labelFontSize: 9,
    titleFontSize: 9,
    titleFontWeight: 400 as const,
    labelColor: "#8b968f",
    titleColor: "#8b968f",
    domainColor: "#cdd6ce",
    tickColor: "#cdd6ce",
    gridColor: "#e3e9e3",
    labelPadding: 4,
  },
  legend: {
    labelFontSize: 9,
    titleFontSize: 9,
    titleFontWeight: 400 as const,
    labelColor: "#5a6660",
    titleColor: "#8b968f",
    symbolSize: 60,
  },
  view: { stroke: null },
  range: {
    // Brine teal leads; the rest are drawn from the same mineral family so
    // a five-region chart still reads as one instrument.
    category: ["#1f6f63", "#b4611a", "#4b7ba8", "#7a5ea8", "#8a8f3c", "#a8506b"],
    heatmap: { scheme: "teals" },
  },
  text: { color: "#16201c" },
  title: { fontSize: 11, fontWeight: 600 as const, color: "#16201c", anchor: "start" as const },
};

export default function VegaChart({
  spec,
  rows,
}: {
  spec: Record<string, unknown>;
  rows: Record<string, unknown>[];
}) {
  // Rows are inlined rather than bound as a named dataset: the named-data
  // handoff silently yields an empty view (Vega reports an infinite extent
  // rather than an error), and there is nothing here worth that risk.
  const full = useMemo(
    () => ({
      ...spec,
      data: { values: rows },
      config: CONFIG,
      width: "container",
      height: "container",
      autosize: { type: "fit", contains: "padding", resize: true },
    }),
    [spec, rows],
  );

  if (!rows.length) {
    return <p className="eyebrow">No rows matched.</p>;
  }

  return (
    <div style={{ width: "100%", height: "100%", minHeight: 140 }}>
      <VegaLite
        spec={full as never}
        actions={false}
        renderer="svg"
        style={{ width: "100%", height: "100%" }}
      />
    </div>
  );
}
