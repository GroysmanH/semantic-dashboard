import { useMemo } from "react";
import { VegaLite } from "react-vega";
import { KAZAKHSTAN } from "../assets/kazakhstan";

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

    // Vega-Lite sizes a facet by its panels, not by its container: passing
    // width "container" makes it warn and fall back to a default, which
    // renders a technically correct spec as a mess. Layered views -- the
    // map -- take container sizing normally.
    if ("facet" in filled) {
      return {
        ...filled,
        config: CONFIG,
        spec: {
          ...(filled.spec as Record<string, unknown>),
          width: 150,
          height: 110,
        },
      };
    }

    return {
      ...filled,
      config: CONFIG,
      width: "container",
      height: "container",
      autosize: { type: "fit", contains: "padding", resize: true },
    };
  }, [spec, rows]);

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
