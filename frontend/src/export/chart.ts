import {
  chartConfig,
  chartGeneration,
  prepareChartSpec,
  type ChartBounds,
  type ChartConfig,
} from "../charts/prepare";
import { chartPng, type VegaView } from "./png";

export { chartConfig } from "../charts/prepare";

export type MountedChartView = { generation: string; view: VegaView };
export type ChartSnapshot = () => Promise<string>;

export interface SnapshotChartOptions {
  spec: Record<string, unknown>;
  rows: Record<string, unknown>[];
  bounds: ChartBounds;
  config?: ChartConfig;
  mounted?: MountedChartView | null;
  scale?: number;
}

/** Rasterise the current chart. A mounted view is an optimisation only; a
 * generation mismatch always takes the self-contained off-screen path. */
export async function snapshotChart({
  spec,
  rows,
  bounds,
  config = chartConfig(),
  mounted = null,
  scale = 2,
}: SnapshotChartOptions): Promise<string> {
  const generation = chartGeneration(spec, rows, bounds, config);
  if (mounted?.generation === generation) return chartPng(mounted.view, scale);

  const host = document.createElement("div");
  host.setAttribute("aria-hidden", "true");
  Object.assign(host.style, {
    position: "fixed",
    left: "-10000px",
    top: "0",
    width: `${Math.max(1, bounds.width)}px`,
    height: `${Math.max(1, bounds.height)}px`,
    background: "#ffffff",
    pointerEvents: "none",
  });
  document.body.appendChild(host);

  let temporary: import("vega").View | null = null;
  let renderingFailed = false;
  try {
    // Keep Vega's canvas feature detection out of ordinary page/test startup;
    // the engine is only needed when the mounted SVG cannot serve the spec.
    const [{ View, parse }, { compile }] = await Promise.all([
      import("vega"),
      import("vega-lite"),
    ]);
    const prepared = prepareChartSpec(spec, rows, bounds, config);
    const compiled = compile(prepared as never).spec;
    temporary = new View(parse(compiled), {
      container: host,
      renderer: "svg",
      hover: false,
    });
    await temporary.runAsync();
    return await chartPng(temporary, scale);
  } catch (error) {
    renderingFailed = true;
    throw error;
  } finally {
    try {
      try {
        temporary?.finalize();
      } catch (finalizeError) {
        // A cleanup failure is useful only when it is the first failure. If
        // rendering already failed, preserve that actionable root cause.
        if (!renderingFailed) throw finalizeError;
      }
    } finally {
      host.remove();
    }
  }
}
