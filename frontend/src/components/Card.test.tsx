import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Card as CardT } from "../api/client";
import type { VegaView } from "../export/png";
import Card from "./Card";

const chart = vi.hoisted(() => ({ props: {} as { onView?: (view: VegaView | null) => void } }));
const csvBlob = vi.hoisted(() => vi.fn());
const apiMocks = vi.hoisted(() => ({
  refreshCard: vi.fn(),
  undoCard: vi.fn(),
  deleteCard: vi.fn(),
  ask: vi.fn(),
}));

vi.mock("./VegaChart", () => ({
  default: (props: { onView?: (view: VegaView | null) => void }) => {
    chart.props = props;
    return <div data-testid="chart" />;
  },
}));
vi.mock("../export/csv", () => ({ csvBlob }));
vi.mock("../api/client", () => ({ api: apiMocks }));

function readyCard(spec: Record<string, unknown> = { mark: "line" }): CardT {
  return {
    id: "card-1",
    board_id: "board-1",
    title: "Oil by month",
    semantic_query: null,
    chart_hint: null,
    state: "ready",
    can_undo: false,
    layout: { x: 0, y: 0, w: 6, h: 9 },
    ttl_seconds: 900,
    render: {
      state: "ready",
      restatement: "Sum of oil production by month.",
      rows: [{ month: "2026-01-01", oil: 12 }],
      row_count: 1,
      vega_spec: spec,
    },
  };
}

function brokenCard(): CardT {
  return {
    ...readyCard(),
    state: "broken",
    render: { state: "broken", error: "Layer changed" },
  };
}

const baseProps = {
  examples: [],
  providers: { default: "gemini" as const, available: ["gemini" as const] },
  selected: false,
  sqlOpen: false,
  resizing: false,
  onSelect: vi.fn(),
  onMoveIntent: vi.fn(),
  onSqlToggle: vi.fn(),
  onTransientHeight: vi.fn(),
  onChanged: vi.fn(),
  onView: vi.fn(),
};

describe("Card export state", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    csvBlob.mockReset();
    baseProps.onView.mockReset();
  });

  it("surfaces a synchronous CSV export failure in the card notice", async () => {
    const user = userEvent.setup();
    csvBlob.mockImplementation(() => { throw new Error("CSV export failed"); });
    render(<Card {...baseProps} card={readyCard()} />);

    await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
    await user.click(screen.getByRole("menuitem", { name: "CSV" }));

    expect(await screen.findByText("CSV export failed")).toBeVisible();
  });

  it("does not offer a stale PNG after the card stops rendering a chart", async () => {
    const liveView = { toImageURL: vi.fn() } as unknown as VegaView;
    const { rerender } = render(<Card {...baseProps} card={readyCard()} />);
    act(() => chart.props.onView?.(liveView));
    expect(screen.getByRole("button", { name: "Export Oil by month" })).toBeVisible();

    rerender(<Card {...baseProps} card={brokenCard()} />);

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Export Oil by month" })).not.toBeInTheDocument();
      expect(baseProps.onView).toHaveBeenCalledWith(null);
    });
  });

  it("disables PNG until the replacement spec supplies its own Vega view", async () => {
    const user = userEvent.setup();
    const liveView = { toImageURL: vi.fn() } as unknown as VegaView;
    const firstSpec = { mark: "line" };
    const { rerender } = render(<Card {...baseProps} card={readyCard(firstSpec)} />);
    act(() => chart.props.onView?.(liveView));

    rerender(<Card {...baseProps} card={readyCard({ mark: "bar" })} />);
    await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
    const menu = screen.getByRole("menu", { name: "Export Oil by month" });

    await waitFor(() => {
      expect(within(menu).getByRole("menuitem", { name: "PNG" })).toBeDisabled();
      expect(baseProps.onView).toHaveBeenCalledWith(null);
    });
  });
});
