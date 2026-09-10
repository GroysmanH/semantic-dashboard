import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import type { Card as CardT } from "../api/client";
import type { VegaView } from "../export/png";
import Card from "./Card";

const captured = vi.hoisted(() => ({
  onNewViews: [] as Array<(view: unknown) => void>,
}));
const exportMocks = vi.hoisted(() => ({
  chartConfig: vi.fn(() => ({ background: "transparent" })),
  snapshotChart: vi.fn(),
  download: vi.fn(),
}));
const apiMocks = vi.hoisted(() => ({
  refreshCard: vi.fn(),
  undoCard: vi.fn(),
  deleteCard: vi.fn(),
  dismissCardExchange: vi.fn(),
  ask: vi.fn(),
}));

vi.mock("react-vega", () => ({
  VegaLite: ({ onNewView }: { onNewView: (view: unknown) => void }) => {
    captured.onNewViews.push(onNewView);
    return <div data-testid="vega-lite" />;
  },
}));
vi.mock("../export/chart", async (importOriginal) => ({
  ...await importOriginal<typeof import("../export/chart")>(),
  chartConfig: exportMocks.chartConfig,
  snapshotChart: exportMocks.snapshotChart,
}));
vi.mock("../export/png", async (importOriginal) => ({
  ...await importOriginal<typeof import("../export/png")>(),
  download: exportMocks.download,
}));
vi.mock("../api/client", () => ({ api: apiMocks }));

function readyCard(
  spec: Record<string, unknown>,
  rows: Record<string, unknown>[] = [{ month: "2026-01-01", oil: 12 }],
): CardT {
  return {
    id: "card-1",
    board_id: "board-1",
    title: "Oil by month",
    semantic_query: null,
    chart_hint: null,
    state: "ready",
    can_undo: false,
    auto_size_pending: false,
    layout: { x: 0, y: 0, w: 6, h: 9 },
    ttl_seconds: 900,
    render: {
      state: "ready",
      restatement: "Sum of oil production by month.",
      rows,
      row_count: rows.length,
      vega_spec: spec,
    },
  };
}

const baseProps = {
  examples: [],
  provider: "gemini" as const,
  providers: ["gemini" as const],
  onProviderChange: vi.fn(),
  strongAvailable: true,
  selected: false,
  sqlOpen: false,
  resizing: false,
  onSelect: vi.fn(),
  onMoveIntent: vi.fn(),
  onSqlToggle: vi.fn(),
  onTransientHeight: vi.fn(),
  onDuplicate: vi.fn(),
  onChanged: vi.fn(),
};

beforeEach(() => {
  captured.onNewViews = [];
  Object.values(exportMocks).forEach((mock) => mock.mockReset());
  exportMocks.chartConfig.mockReturnValue({ background: "transparent" });
  exportMocks.snapshotChart.mockResolvedValue("data:image/png;base64,current");
  Object.values(apiMocks).forEach((mock) => mock.mockReset());
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, top: 0, left: 0, right: 640, bottom: 240,
    width: 640, height: 240, toJSON: () => ({}),
  } as DOMRect);
});

it("never exports a late view created for the previous spec", async () => {
  const user = userEvent.setup();
  const firstSpec = { data: { name: "table" }, mark: "line" };
  const secondSpec = { data: { name: "table" }, mark: "bar" };
  const staleView = { toImageURL: vi.fn() } as unknown as VegaView;
  const { rerender } = render(<Card {...baseProps} card={readyCard(firstSpec)} />);
  await waitFor(() => expect(captured.onNewViews.length).toBeGreaterThan(0));
  const oldOnNewView = captured.onNewViews.at(-1)!;

  rerender(<Card {...baseProps} card={readyCard(secondSpec)} />);
  await waitFor(() => expect(captured.onNewViews.at(-1)).not.toBe(oldOnNewView));
  act(() => oldOnNewView(staleView));

  await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
  await user.click(screen.getByRole("menuitem", { name: "PNG" }));

  await waitFor(() => expect(exportMocks.snapshotChart).toHaveBeenCalledWith(
    expect.objectContaining({ spec: secondSpec, mounted: null }),
  ));
  expect(staleView.toImageURL).not.toHaveBeenCalled();
});

it("never exports an already-mounted view after rows change under the same spec", async () => {
  const user = userEvent.setup();
  const spec = { data: { name: "table" }, mark: "line" };
  const oldView = { toImageURL: vi.fn() } as unknown as VegaView;
  const { rerender } = render(<Card {...baseProps} card={readyCard(
    spec, [{ month: "2026-01-01", oil: 12 }],
  )} />);
  await waitFor(() => expect(captured.onNewViews.length).toBeGreaterThan(0));
  act(() => captured.onNewViews.at(-1)!(oldView));

  rerender(<Card {...baseProps} card={readyCard(
    spec, [{ month: "2026-01-01", oil: 99 }],
  )} />);
  await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
  await user.click(screen.getByRole("menuitem", { name: "PNG" }));

  await waitFor(() => expect(exportMocks.snapshotChart).toHaveBeenCalledWith(
    expect.objectContaining({
      rows: [{ month: "2026-01-01", oil: 99 }],
      mounted: null,
    }),
  ));
  expect(oldView.toImageURL).not.toHaveBeenCalled();
});

it("offers PNG for a reloaded ready card with zero rows", async () => {
  const user = userEvent.setup();
  const spec = { data: { name: "table" }, mark: "line" };
  render(<Card {...baseProps} card={readyCard(spec, [])} />);

  await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
  await user.click(screen.getByRole("menuitem", { name: "PNG" }));

  await waitFor(() => expect(exportMocks.snapshotChart).toHaveBeenCalledWith(
    expect.objectContaining({ spec, rows: [] }),
  ));
});
