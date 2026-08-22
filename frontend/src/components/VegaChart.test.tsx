import { useState } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { VegaView } from "../export/png";
import VegaChart from "./VegaChart";

const captured = vi.hoisted(() => ({
  spec: {} as Record<string, unknown>,
  onNewViews: [] as Array<(view: unknown) => void>,
}));

vi.mock("react-vega", () => ({
  VegaLite: ({
    spec,
    onNewView,
  }: {
    spec: Record<string, unknown>;
    onNewView: (view: unknown) => void;
  }) => {
    captured.spec = spec;
    captured.onNewViews.push(onNewView);
    return <div data-testid="vega-lite" />;
  },
}));

function mediaQueryList(query: string): MediaQueryList {
  return {
    matches: query.includes("dark"),
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  };
}

describe("VegaChart presentation sizing", () => {
  beforeEach(() => {
    captured.spec = {};
    captured.onNewViews = [];
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 760,
      bottom: 320,
      width: 760,
      height: 320,
      toJSON: () => ({}),
    } as DOMRect);
  });

  it("keeps the Vega new-view listener stable when delivering a view rerenders its parent", async () => {
    const spec = {
      data: { name: "table" },
      mark: "bar",
      encoding: { x: { field: "value", type: "quantitative" } },
    };
    const rows = [{ value: 12 }];
    const delivered = { toImageURL: vi.fn() } as unknown as VegaView;

    function Harness() {
      const [, setView] = useState<VegaView | null>(null);
      return <VegaChart spec={spec} rows={rows} onView={setView} />;
    }

    render(<Harness />);
    await waitFor(() => expect(captured.onNewViews.length).toBeGreaterThan(0));
    const firstListener = captured.onNewViews.at(-1)!;

    act(() => firstListener(delivered));
    await waitFor(() => expect(captured.onNewViews.length).toBeGreaterThan(1));

    expect(captured.onNewViews.at(-1)).toBe(firstListener);
  });

  it("keeps short rankings compact instead of stretching one bar through the card", async () => {
    render(
      <VegaChart
        rows={[{ region: "West Kazakhstan", gas: 55_000_000 }]}
        spec={{
          data: { name: "table" },
          usermeta: { presentation: "ranked", idealHeight: 84 },
          mark: { type: "bar", size: 22 },
          encoding: {
            y: { field: "region", type: "nominal" },
            x: { field: "gas", type: "quantitative" },
          },
        }}
      />,
    );

    await waitFor(() => expect(captured.spec.height).toBe(84));
    expect(captured.spec.width).toBeGreaterThan(600);
  });

  it("gives vertically faceted measures the available plot width", async () => {
    render(
      <VegaChart
        rows={[
          { month: "2026-01-01", oil: 10, gas: 20 },
          { month: "2026-02-01", oil: 12, gas: 24 },
        ]}
        spec={{
          data: { name: "table" },
          transform: [{ fold: ["oil", "gas"], as: ["measure", "value"] }],
          facet: { row: { field: "measure", type: "nominal" } },
          spec: {
            mark: "line",
            encoding: {
              x: { field: "month", type: "temporal" },
              y: { field: "value", type: "quantitative" },
            },
          },
          resolve: { scale: { y: "independent" } },
        }}
      />,
    );

    await waitFor(() => {
      const child = captured.spec.spec as Record<string, unknown>;
      expect(child.width).toBeGreaterThan(500);
      expect(child.height).toBeGreaterThanOrEqual(90);
      expect(child.height).toBeLessThanOrEqual(140);
    });
  });

  it("does not follow the operating system dark-mode preference", () => {
    const matchMedia = vi.spyOn(window, "matchMedia").mockImplementation(mediaQueryList);

    render(
      <VegaChart
        rows={[{ total: 12 }]}
        spec={{
          data: { name: "table" },
          usermeta: { presentation: "kpi" },
          mark: "text",
          encoding: { text: { field: "total", type: "quantitative" } },
        }}
      />,
    );

    expect(matchMedia).not.toHaveBeenCalled();
    const config = captured.spec.config as {
      range: { category: string[]; heatmap: { scheme: string } };
    };
    // The series come out of the page's own palette, so a chart cannot end
    // up coloured by something the rest of the interface has never heard
    // of -- and cannot quietly follow the operating system instead.
    expect(config.range.category[0]).toBe("#00539b");
    expect(config.range.category.length).toBeGreaterThanOrEqual(8);
    expect(config.range.heatmap.scheme).toBe("blues");
  });

  it("never asks a narrow card to render a wider unit chart", async () => {
    vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 190,
      bottom: 240,
      width: 190,
      height: 240,
      toJSON: () => ({}),
    } as DOMRect);

    render(
      <VegaChart
        rows={[{ region: "West", oil: 10 }]}
        spec={{
          data: { name: "table" },
          usermeta: { presentation: "ranked", idealHeight: 84 },
          mark: "bar",
          encoding: {
            y: { field: "region", type: "nominal" },
            x: { field: "oil", type: "quantitative" },
          },
        }}
      />,
    );

    await waitFor(() => expect(captured.spec.width).toBeLessThanOrEqual(182));
    expect(captured.spec.width).toBeGreaterThan(0);
  });

  it("keeps the rendered chart visible and scales it during live card resize", async () => {
    let box = {
      x: 0, y: 0, top: 0, left: 0, right: 760, bottom: 320,
      width: 760, height: 320, toJSON: () => ({}),
    } as DOMRect;
    let notify: ResizeObserverCallback | undefined;
    class ControlledResizeObserver implements ResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        notify = callback;
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", ControlledResizeObserver);
    vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockImplementation(() => box);

    const chart = (
      <VegaChart
        rows={[{ region: "West", oil: 10 }, { region: "East", oil: 8 }]}
        spec={{
          data: { name: "table" },
          mark: "bar",
          encoding: {
            y: { field: "region", type: "nominal" },
            x: { field: "oil", type: "quantitative" },
          },
        }}
      />
    );
    const { container, rerender } = render(chart);
    await waitFor(() => expect(captured.spec.width).toBe(752));

    rerender({ ...chart, props: { ...chart.props, resizing: true } });
    box = {
      ...box,
      right: 380,
      bottom: 160,
      width: 380,
      height: 160,
    } as DOMRect;
    act(() => {
      notify?.([{
        target: container.querySelector(".chart-shell")!,
        contentRect: box,
      } as ResizeObserverEntry], {} as ResizeObserver);
    });

    expect(screen.getByTestId("vega-lite")).toBeVisible();
    expect(captured.spec.width).toBe(752);
    expect(container.querySelector(".chart-render-frame")).toHaveStyle({
      transform: "scale(0.5, 0.5)",
    });

    rerender({ ...chart, props: { ...chart.props, resizing: false } });
    await waitFor(() => expect(captured.spec.width).toBe(372));
    expect(container.querySelector(".chart-render-frame")).not.toHaveStyle({
      transform: "scale(0.5, 0.5)",
    });
  });

  it("keeps four measure facets readable through internal vertical scrolling", async () => {
    const { container } = render(
      <VegaChart
        rows={[
          { month: "2026-01-01", oil: 10, gas: 20, water: 30, downtime: 2 },
          { month: "2026-02-01", oil: 12, gas: 24, water: 32, downtime: 1 },
        ]}
        spec={{
          data: { name: "table" },
          usermeta: { presentation: "facets" },
          transform: [{
            fold: ["oil", "gas", "water", "downtime"],
            as: ["measure", "value"],
          }],
          facet: { row: { field: "measure", type: "nominal" } },
          spec: {
            mark: "line",
            encoding: {
              x: { field: "month", type: "temporal" },
              y: { field: "value", type: "quantitative" },
            },
          },
        }}
      />,
    );

    await waitFor(() => {
      expect(container.querySelector(".chart-shell")).toHaveClass("scrollable");
      const child = captured.spec.spec as Record<string, unknown>;
      expect(child.height).toBeGreaterThanOrEqual(84);
    });
  });

  it("subtracts facet headers instead of overflowing a narrow card", async () => {
    vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 190,
      bottom: 320,
      width: 190,
      height: 320,
      toJSON: () => ({}),
    } as DOMRect);

    render(
      <VegaChart
        rows={[
          { month: "2026-01-01", oil: 10, gas: 20 },
          { month: "2026-02-01", oil: 12, gas: 24 },
        ]}
        spec={{
          data: { name: "table" },
          transform: [{ fold: ["oil", "gas"], as: ["measure", "value"] }],
          facet: { row: { field: "measure", type: "nominal" } },
          spec: {
            mark: "line",
            encoding: {
              x: { field: "month", type: "temporal" },
              y: { field: "value", type: "quantitative" },
            },
          },
        }}
      />,
    );

    await waitFor(() => {
      const child = captured.spec.spec as Record<string, unknown>;
      expect(child.width).toBeGreaterThan(0);
      expect(child.width).toBeLessThanOrEqual(74);
    });
  });

  it("wraps four KPIs in a narrow card instead of shrinking their values into each other", async () => {
    vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockReturnValue({
      x: 0,
      y: 0,
      top: 0,
      left: 0,
      right: 190,
      bottom: 320,
      width: 190,
      height: 320,
      toJSON: () => ({}),
    } as DOMRect);

    const unit = (field: string) => ({
      layer: [{
        mark: { type: "text", fontSize: 42 },
        encoding: { text: { field, type: "quantitative" } },
      }],
    });
    const { container } = render(
      <VegaChart
        rows={[{ oil: 10, gas: 20, water: 30, downtime: 2 }]}
        spec={{
          data: { name: "table" },
          usermeta: { presentation: "kpi" },
          hconcat: [unit("oil"), unit("gas"), unit("water"), unit("downtime")],
        }}
      />,
    );

    await waitFor(() => {
      expect(captured.spec).not.toHaveProperty("hconcat");
      expect(captured.spec.concat).toHaveLength(4);
      expect(captured.spec.columns).toBe(1);
      expect(container.querySelector(".chart-shell")).toHaveClass("scrollable");
    });
    for (const child of captured.spec.concat as Array<Record<string, unknown>>) {
      expect(child.width).toBeGreaterThanOrEqual(150);
    }
  });
});
