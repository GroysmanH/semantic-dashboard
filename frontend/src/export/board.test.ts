import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Card } from "../api/client";
import { boardPng } from "./board";

function card(id: string, title: string, x: number, rows = [{ value: 1 }]): Card {
  return {
    id,
    board_id: "board-1",
    title,
    semantic_query: null,
    chart_hint: null,
    state: "ready",
    can_undo: false,
    auto_size_pending: false,
    layout: { x, y: 0, w: 6, h: 9 },
    ttl_seconds: 900,
    render: {
      state: "ready",
      restatement: `${title} restatement`,
      rows,
      row_count: rows.length,
      vega_spec: { data: { name: "table" }, mark: "bar" },
    },
  };
}

describe("boardPng reliability", () => {
  const drawImage = vi.fn();
  const toBlob = vi.fn((callback: BlobCallback) => callback(new Blob(["png"])));

  beforeEach(() => {
    drawImage.mockClear();
    toBlob.mockClear();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
      scale: vi.fn(),
      fillRect: vi.fn(),
      strokeRect: vi.fn(),
      fillText: vi.fn(),
      measureText: vi.fn((text: string) => ({ width: text.length * 6 })),
      save: vi.fn(),
      beginPath: vi.fn(),
      rect: vi.fn(),
      clip: vi.fn(),
      drawImage,
      restore: vi.fn(),
    } as unknown as CanvasRenderingContext2D);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(toBlob);
    vi.stubGlobal("Image", class {
      width = 640;
      height = 240;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_value: string) {
        queueMicrotask(() => this.onload?.());
      }
    });
  });

  it("waits for every ready visual card before composing all of them", async () => {
    let releaseSecond!: (value: string) => void;
    const second = new Promise<string>((resolve) => { releaseSecond = resolve; });
    const exportPromise = boardPng("Operations", [
      { card: card("one", "Oil by month", 0), snapshot: async () => "data:image/png;base64,one" },
      { card: card("two", "Gas by month", 6), snapshot: () => second },
    ]);

    await Promise.resolve();
    expect(toBlob).not.toHaveBeenCalled();
    releaseSecond("data:image/png;base64,two");

    await expect(exportPromise).resolves.toBeInstanceOf(Blob);
    expect(drawImage).toHaveBeenCalledTimes(2);
  });

  it("names every failed ready card and aborts instead of exporting a partial dashboard", async () => {
    await expect(boardPng("Operations", [
      { card: card("one", "Oil by month", 0), snapshot: async () => "data:image/png;base64,one" },
      { card: card("two", "Gas by month", 6), snapshot: null },
      { card: card("three", "Water injection", 0), snapshot: async () => { throw new Error("failed"); } },
    ])).rejects.toThrow("Could not export Gas by month and Water injection");

    expect(drawImage).not.toHaveBeenCalled();
    expect(toBlob).not.toHaveBeenCalled();
  });

  it("awaits and includes ready zero-row cards instead of filtering them out", async () => {
    const zero = card("zero", "No matching wells", 0, []);
    const snapshot = vi.fn().mockResolvedValue("data:image/png;base64,zero");

    await expect(boardPng("Operations", [
      { card: zero, snapshot },
    ])).resolves.toBeInstanceOf(Blob);

    expect(snapshot).toHaveBeenCalledOnce();
    expect(drawImage).toHaveBeenCalledOnce();
  });
});
