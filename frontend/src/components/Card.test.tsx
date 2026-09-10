import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Card as CardT } from "../api/client";
import type { VegaView } from "../export/png";
import Card from "./Card";

const chart = vi.hoisted(() => ({ props: {} as { onView?: (view: VegaView | null) => void } }));
const csvBlob = vi.hoisted(() => vi.fn());
const pngMocks = vi.hoisted(() => ({ chartPng: vi.fn(), download: vi.fn() }));
const snapshotMocks = vi.hoisted(() => ({
  chartConfig: vi.fn(() => ({ background: "transparent" })),
  snapshotChart: vi.fn(),
}));
const apiMocks = vi.hoisted(() => ({
  refreshCard: vi.fn(),
  undoCard: vi.fn(),
  deleteCard: vi.fn(),
  dismissCardExchange: vi.fn(),
  ask: vi.fn(),
}));

vi.mock("./VegaChart", () => ({
  default: (props: { onView?: (view: VegaView | null) => void }) => {
    chart.props = props;
    return <div data-testid="chart" />;
  },
}));
vi.mock("../export/csv", () => ({ csvBlob }));
vi.mock("../export/chart", () => snapshotMocks);
vi.mock("../export/png", async (importOriginal) => ({
  ...await importOriginal<typeof import("../export/png")>(),
  chartPng: pngMocks.chartPng,
  download: pngMocks.download,
}));
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
    auto_size_pending: false,
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

function emptyCard(): CardT {
  const { render: _unused, ...rest } = readyCard();
  return { ...rest, title: "", state: "empty" };
}

function emptyCardWaitingForReply(
  kind: "clarify" | "refused" = "clarify",
): CardT {
  return {
    ...emptyCard(),
    pending_clarification: {
      kind,
      question: kind === "clarify" ? "Oil or gas?" : "No per-region ranking.",
      asked: "show me production",
    },
  } as CardT;
}

function readyCardWaitingForReply(): CardT {
  return {
    ...readyCard(),
    can_undo: true,
    pending_clarification: {
      kind: "clarify",
      question: "Oil or gas?",
      asked: "show me production",
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
  onView: vi.fn(),
};

describe("Card export state", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    csvBlob.mockReset();
    pngMocks.chartPng.mockReset();
    pngMocks.download.mockReset();
    snapshotMocks.snapshotChart.mockReset();
    baseProps.onView.mockReset();
  });

  it("offers both PNG and CSV immediately for a hydrated ready card", async () => {
    const user = userEvent.setup();
    render(<Card {...baseProps} card={readyCard()} />);

    await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
    const menu = screen.getByRole("menu", { name: "Export Oil by month" });
    expect(within(menu).getByRole("menuitem", { name: "PNG" })).toBeEnabled();
    expect(within(menu).getByRole("menuitem", { name: "CSV" })).toBeEnabled();
  });

  it("exports every hydrated result row rather than the chart-sized subset", async () => {
    const user = userEvent.setup();
    const card = readyCard();
    card.render!.rows = [
      { month: "2026-01-01", oil: 12 },
      { month: "2026-02-01", oil: 13 },
    ];
    card.render!.chart_rows = [{ month: "2026-01-01", oil: 25 }];
    csvBlob.mockReturnValue(new Blob(["csv"]));
    render(<Card {...baseProps} card={card} />);

    await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
    await user.click(screen.getByRole("menuitem", { name: "CSV" }));

    expect(csvBlob).toHaveBeenCalledWith(card.render!.rows);
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

  it("replaces a stale view through the off-screen PNG path", async () => {
    const user = userEvent.setup();
    const liveView = { toImageURL: vi.fn() } as unknown as VegaView;
    const firstSpec = { mark: "line" };
    snapshotMocks.snapshotChart.mockResolvedValueOnce("data:image/png;base64,replacement");
    const { rerender } = render(<Card {...baseProps} card={readyCard(firstSpec)} />);
    act(() => chart.props.onView?.(liveView));

    const replacementSpec = { mark: "bar" };
    rerender(<Card {...baseProps} card={readyCard(replacementSpec)} />);
    await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
    await user.click(screen.getByRole("menuitem", { name: "PNG" }));

    await waitFor(() => expect(snapshotMocks.snapshotChart).toHaveBeenCalledWith(
      expect.objectContaining({ spec: replacementSpec, mounted: null }),
    ));
    expect(pngMocks.download).toHaveBeenCalledWith(
      "data:image/png;base64,replacement",
      expect.stringMatching(/^oil-by-month-.*\.png$/),
    );
  });
});


describe("Card exchanges", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    pngMocks.chartPng.mockReset();
    pngMocks.download.mockReset();
    baseProps.onChanged.mockReset();
  });

  it("turns the composer into a reply box once the card has asked something", async () => {
    const user = userEvent.setup();
    apiMocks.ask.mockResolvedValue({
      state: "clarify",
      message: "By 'top' do you mean oil production or gas production?",
    });
    render(<Card {...baseProps} card={emptyCard()} />);

    await user.type(screen.getByLabelText("Ask for a chart…"), "top well in each region");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByText("By 'top' do you mean oil production or gas production?"))
      .toBeVisible();
    expect(screen.getByLabelText("Answer that…")).toBeVisible();
    expect(apiMocks.ask).toHaveBeenLastCalledWith(
      "top well in each region", "card-1", false, "gemini", false);
  });

  it("restores a saved exchange after the card reloads", async () => {
    const user = userEvent.setup();
    apiMocks.ask.mockResolvedValueOnce({ state: "ready", rows: [], changed: [] });
    render(<Card {...baseProps} card={emptyCardWaitingForReply()} />);

    expect(screen.getByText("Oil or gas?")).toBeVisible();
    await user.type(screen.getByLabelText("Answer that…"), "oil");
    await user.click(screen.getByRole("button", { name: "Reply" }));

    expect(apiMocks.ask).toHaveBeenLastCalledWith(
      "oil", "card-1", false, "gemini", true);
  });

  it("keeps the saved exchange replyable when sending the reply fails", async () => {
    const user = userEvent.setup();
    apiMocks.ask.mockRejectedValueOnce(new Error("Model unavailable"));
    render(<Card {...baseProps} card={emptyCardWaitingForReply()} />);

    await user.type(screen.getByLabelText("Answer that…"), "oil");
    await user.click(screen.getByRole("button", { name: "Reply" }));

    expect(await screen.findByText("Model unavailable")).toBeVisible();
    expect(screen.getByText("Oil or gas?")).toBeVisible();
    expect(screen.getByLabelText("Answer that…")).toBeVisible();
  });

  it("does not hide a saved exchange after exporting its chart", async () => {
    const user = userEvent.setup();
    const liveView = { toImageURL: vi.fn() } as unknown as VegaView;
    pngMocks.chartPng.mockResolvedValueOnce("data:image/png;base64,chart");
    render(<Card {...baseProps} selected card={readyCardWaitingForReply()} />);
    act(() => chart.props.onView?.(liveView));

    await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
    await user.click(screen.getByRole("menuitem", { name: "PNG" }));

    expect(screen.getByText("Oil or gas?")).toBeVisible();
    expect(screen.getByLabelText("Answer that…")).toBeVisible();
  });

  it("keeps a saved exchange replyable when undo fails", async () => {
    const user = userEvent.setup();
    apiMocks.undoCard.mockRejectedValueOnce(new Error("Undo failed"));
    render(<Card {...baseProps} selected card={readyCardWaitingForReply()} />);

    await user.click(screen.getByRole("button", { name: "Undo the previous edit" }));

    expect(await screen.findByText("Undo failed")).toBeVisible();
    expect(screen.getByText("Oil or gas?")).toBeVisible();
    expect(screen.getByLabelText("Answer that…")).toBeVisible();
  });

  it("shows an operational refusal as an error, not a replyable exchange", async () => {
    const user = userEvent.setup();
    apiMocks.ask.mockResolvedValueOnce({
      state: "refused",
      message: "The free model is rate limited. Try again in a moment.",
      replyable: false,
    });
    render(<Card {...baseProps} card={emptyCard()} />);

    await user.type(screen.getByLabelText("Ask for a chart…"), "oil by month");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    expect(await screen.findByText("The free model is rate limited. Try again in a moment."))
      .toBeVisible();
    expect(screen.queryByRole("button", { name: "Reply" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Ask for a chart…")).toBeVisible();
  });

  it("sends the next message as a reply to what the card said", async () => {
    const user = userEvent.setup();
    apiMocks.ask.mockResolvedValueOnce({ state: "refused", message: "No per-region ranking." });
    apiMocks.ask.mockResolvedValueOnce({ state: "ready", rows: [], changed: [] });
    render(<Card {...baseProps} card={emptyCard()} />);

    await user.type(screen.getByLabelText("Ask for a chart…"), "top well in each region");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    await user.type(await screen.findByLabelText("Reply to that…"), "then rank them overall");
    await user.click(screen.getByRole("button", { name: "Reply" }));

    expect(apiMocks.ask).toHaveBeenLastCalledWith(
      "then rank them overall", "card-1", false, "gemini", true);
  });

  it("lets the asker walk away, and does not answer the old question with the new one", async () => {
    const user = userEvent.setup();
    apiMocks.ask.mockResolvedValueOnce({ state: "clarify", message: "Oil or gas?" });
    apiMocks.ask.mockResolvedValueOnce({ state: "ready", rows: [], changed: [] });
    render(<Card {...baseProps} card={emptyCard()} />);

    await user.type(screen.getByLabelText("Ask for a chart…"), "show me production");
    await user.click(screen.getByRole("button", { name: "Ask" }));
    await user.click(await screen.findByRole("button", { name: "Ask something else" }));

    expect(screen.queryByText("Oil or gas?")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Ask for a chart…"), "downtime by month");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    expect(apiMocks.ask).toHaveBeenLastCalledWith(
      "downtime by month", "card-1", false, "gemini", false);
  });

  it("persists walking away so the old exchange cannot return on reload", async () => {
    const user = userEvent.setup();
    apiMocks.dismissCardExchange.mockResolvedValueOnce(undefined);
    render(<Card {...baseProps} card={emptyCardWaitingForReply("refused")} />);

    await user.click(screen.getByRole("button", { name: "Ask something else" }));

    await waitFor(() => {
      expect(apiMocks.dismissCardExchange).toHaveBeenCalledWith("card-1");
      expect(screen.queryByText("No per-region ranking.")).not.toBeInTheDocument();
      expect(baseProps.onChanged).toHaveBeenCalled();
    });
  });

  it("hides the examples while a question is waiting on an answer", async () => {
    const user = userEvent.setup();
    apiMocks.ask.mockResolvedValue({ state: "clarify", message: "Oil or gas?" });
    render(<Card {...baseProps} card={emptyCard()} examples={["oil production by month"]} />);

    expect(screen.getByRole("button", { name: "oil production by month" })).toBeVisible();

    await user.type(screen.getByLabelText("Ask for a chart…"), "show me production");
    await user.click(screen.getByRole("button", { name: "Ask" }));

    await waitFor(() => {
      // An example clicked into a reply box would answer the card's
      // question with a sentence that was never meant for it.
      expect(screen.queryByRole("button", { name: "oil production by month" }))
        .not.toBeInTheDocument();
    });
  });
});
