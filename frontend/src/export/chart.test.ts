import { beforeEach, describe, expect, it, vi } from "vitest";
import type { VegaView } from "./png";

const engine = vi.hoisted(() => ({
  compile: vi.fn(() => ({ spec: { marks: [] } })),
  parse: vi.fn((spec: unknown) => ({ runtime: spec })),
  toImageError: null as Error | null,
  finalizeError: null as Error | null,
  views: [] as Array<{
    runAsync: ReturnType<typeof vi.fn>;
    toImageURL: ReturnType<typeof vi.fn>;
    finalize: ReturnType<typeof vi.fn>;
  }>,
}));

vi.mock("vega-lite", () => ({ compile: engine.compile }));
vi.mock("vega", () => ({
  parse: engine.parse,
  View: class {
    runAsync = vi.fn().mockResolvedValue(undefined);
    toImageURL = vi.fn().mockResolvedValue("data:image/png;base64,temporary");
    finalize = vi.fn(() => {
      if (engine.finalizeError) throw engine.finalizeError;
    });

    constructor() {
      if (engine.toImageError) this.toImageURL.mockRejectedValue(engine.toImageError);
      engine.views.push(this);
    }
  },
}));

import { snapshotChart } from "./chart";
import { chartGeneration } from "../charts/prepare";

const rawSpec = { data: { name: "table" }, mark: "bar" };
const rows = [{ value: 12 }];
const bounds = { width: 640, height: 240 };
const config = { background: "transparent" };

describe("snapshotChart", () => {
  beforeEach(() => {
    engine.compile.mockClear();
    engine.parse.mockClear();
    engine.views.length = 0;
    engine.toImageError = null;
    engine.finalizeError = null;
    document.body.replaceChildren();
  });

  it("uses a mounted view only when it belongs to the current source spec", async () => {
    const current = {
      toImageURL: vi.fn().mockResolvedValue("data:image/png;base64,current"),
    } as unknown as VegaView;

    await expect(snapshotChart({
      spec: rawSpec,
      rows,
      bounds,
      config,
      mounted: { generation: chartGeneration(rawSpec, rows, bounds, config), view: current },
    })).resolves.toBe("data:image/png;base64,current");

    expect(current.toImageURL).toHaveBeenCalledWith("png", 2);
    expect(engine.views).toHaveLength(0);
  });

  it("replaces a stale mounted view with a temporary view prepared from current rows", async () => {
    const stale = {
      toImageURL: vi.fn().mockResolvedValue("data:image/png;base64,stale"),
    } as unknown as VegaView;
    const staleSpec = { ...rawSpec, mark: "line" };

    await expect(snapshotChart({
      spec: rawSpec,
      rows,
      bounds,
      config,
      mounted: {
        generation: chartGeneration(staleSpec, rows, bounds, config),
        view: stale,
      },
    })).resolves.toBe("data:image/png;base64,temporary");

    expect(stale.toImageURL).not.toHaveBeenCalled();
    expect(engine.compile).toHaveBeenCalledWith(expect.objectContaining({
      data: { values: rows },
      width: 632,
      height: 232,
      config,
    }));
    expect(engine.views[0].runAsync).toHaveBeenCalled();
    expect(engine.views[0].finalize).toHaveBeenCalled();
    expect(document.body.children).toHaveLength(0);
  });

  it("does not reuse a mounted view when the raw spec is unchanged but rows changed", async () => {
    const stale = {
      toImageURL: vi.fn().mockResolvedValue("data:image/png;base64,stale"),
    } as unknown as VegaView;
    const changedRows = [{ value: 99 }];

    await expect(snapshotChart({
      spec: rawSpec,
      rows: changedRows,
      bounds,
      config,
      mounted: {
        generation: chartGeneration(rawSpec, rows, bounds, config),
        view: stale,
      },
    })).resolves.toBe("data:image/png;base64,temporary");

    expect(stale.toImageURL).not.toHaveBeenCalled();
    expect(engine.compile).toHaveBeenCalledWith(expect.objectContaining({
      data: { values: changedRows },
    }));
  });

  it("does not reuse a mounted view after the export bounds changed", async () => {
    const stale = {
      toImageURL: vi.fn().mockResolvedValue("data:image/png;base64,stale"),
    } as unknown as VegaView;

    await expect(snapshotChart({
      spec: rawSpec,
      rows,
      bounds: { width: 800, height: 320 },
      config,
      mounted: {
        generation: chartGeneration(rawSpec, rows, bounds, config),
        view: stale,
      },
    })).resolves.toBe("data:image/png;base64,temporary");

    expect(stale.toImageURL).not.toHaveBeenCalled();
    expect(engine.compile).toHaveBeenCalledWith(expect.objectContaining({
      width: 792,
      height: 312,
    }));
  });

  it("finalizes the temporary view and removes its host when rasterisation fails", async () => {
    engine.toImageError = new Error("Raster failed");
    const pending = snapshotChart({ spec: rawSpec, rows, bounds, config });

    await expect(pending).rejects.toThrow("Raster failed");
    expect(engine.views[0].finalize).toHaveBeenCalled();
    expect(document.body.children).toHaveLength(0);
  });

  it("removes the host when finalize throws and preserves an earlier rendering error", async () => {
    engine.toImageError = new Error("Raster failed first");
    engine.finalizeError = new Error("Finalize failed later");

    await expect(snapshotChart({ spec: rawSpec, rows, bounds, config }))
      .rejects.toThrow("Raster failed first");

    expect(engine.views[0].finalize).toHaveBeenCalled();
    expect(document.body.children).toHaveLength(0);
  });
});
