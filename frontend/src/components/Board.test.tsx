import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Card as CardT, Layout } from "../api/client";
import Board from "./Board";

const grid = vi.hoisted(() => ({ props: {} as Record<string, unknown> }));
const apiMocks = vi.hoisted(() => ({
  getBoard: vi.fn(),
  getCard: vi.fn(),
  addCard: vi.fn(),
  saveLayout: vi.fn(),
  refreshCard: vi.fn(),
  undoCard: vi.fn(),
  deleteCard: vi.fn(),
  ask: vi.fn(),
}));

vi.mock("react-grid-layout", () => ({
  default: (props: { children: ReactNode } & Record<string, unknown>) => {
    grid.props = props;
    return <div data-testid="grid">{props.children}</div>;
  },
}));

vi.mock("./VegaChart", () => ({
  default: () => <div data-testid="chart" />,
}));

vi.mock("../api/client", () => ({ api: apiMocks }));


function readyCard(id: string, title: string, layout: Layout): CardT {
  return {
    id,
    board_id: "board-1",
    title,
    semantic_query: null,
    chart_hint: null,
    state: "ready",
    can_undo: true,
    layout,
    ttl_seconds: 900,
    render: {
      state: "ready",
      restatement: `Sum of production for ${title}, from Daily Production.`,
      row_count: 12,
      data_max_ts: "2026-07-31T00:00:00Z",
      fetched_at: new Date().toISOString(),
      from_cache: true,
      rows: [{ reading_date: "2026-01-01", oil: 10 }],
      vega_spec: { mark: "line" },
      chart_type: "line",
      compiled_sql: "SELECT reading_date, SUM(oil) FROM production GROUP BY 1",
    },
  };
}

function emptyCard(id: string, layout: Layout): CardT {
  return {
    id,
    board_id: "board-1",
    title: "Untitled",
    semantic_query: null,
    chart_hint: null,
    state: "empty",
    can_undo: false,
    layout,
    ttl_seconds: 900,
    render: { state: "empty" },
  };
}

describe("Board visual-first interactions", () => {
  let cards: CardT[];

  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    cards = [
      readyCard("card-1", "Oil by month", { x: 0, y: 0, w: 6, h: 9 }),
      readyCard("card-2", "Gas by month", { x: 0, y: 9, w: 6, h: 9 }),
    ];
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.getBoard.mockImplementation(async () => ({
      id: "board-1",
      title: "Operations",
      cards,
    }));
    apiMocks.getCard.mockImplementation(async (id: string) => cards.find((card) => card.id === id));
    apiMocks.saveLayout.mockResolvedValue(undefined);
    apiMocks.deleteCard.mockResolvedValue(undefined);
    apiMocks.refreshCard.mockResolvedValue(undefined);
    apiMocks.undoCard.mockResolvedValue(undefined);
    apiMocks.ask.mockResolvedValue({ state: "ready" });

    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const height = this.classList.contains("refine")
        ? 84
        : this.classList.contains("sql-drawer") ? 210 : 0;
      return {
        x: 0, y: 0, top: 0, left: 0, right: 0, bottom: height,
        width: 0, height, toJSON: () => ({}),
      } as DOMRect;
    });
  });

  it("reveals one measured edit strip and keeps transient height out of persistence", async () => {
    const user = userEvent.setup();
    const computedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element, pseudoElement) => {
      const styles = computedStyle(element, pseudoElement);
      if (!(element as HTMLElement).classList?.contains("refine")) return styles;
      return new Proxy(styles, {
        get(target, property, receiver) {
          if (property === "marginBottom") return "8px";
          return Reflect.get(target, property, receiver);
        },
      });
    });
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    await user.click(screen.getByRole("heading", { name: "Oil by month" }));
    expect(await screen.findByLabelText("Change this chart…")).toBeVisible();
    await waitFor(() => {
      const first = (grid.props.layout as Array<Layout & { i: string }>).find((item) => item.i === "card-1");
      expect(first?.h).toBe(12);
      expect(grid.props.isDraggable).toBe(true);
      expect(grid.props.isResizable).toBe(false);
      expect(grid.props.compactType).toBeNull();
      expect(grid.props.preventCollision).toBe(false);
      expect(grid.props.allowOverlap).toBe(false);
    });

    await user.click(screen.getByRole("heading", { name: "Gas by month" }));
    expect(screen.getAllByLabelText("Change this chart…")).toHaveLength(1);

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByLabelText("Change this chart…")).not.toBeInTheDocument());
    expect(grid.props.isDraggable).toBe(true);

    const moved = [
      { i: "card-1", x: 6, y: 20, w: 6, h: 9 },
      { i: "card-2", x: 0, y: 0, w: 6, h: 9 },
    ];
    (grid.props.onDragStop as (layout: typeof moved) => void)(moved);
    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledWith("board-1", {
      "card-1": { x: 6, y: 20, w: 6, h: 9 },
      "card-2": { x: 0, y: 0, w: 6, h: 9 },
    }));
  });

  it("closes transient panels at move intent so the selected card can be dragged", async () => {
    const user = userEvent.setup();
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await user.click(await screen.findByRole("heading", { name: "Oil by month" }));
    expect(await screen.findByLabelText("Change this chart…")).toBeVisible();
    expect(grid.props.isResizable).toBe(false);

    fireEvent.pointerDown(screen.getAllByLabelText("Drag to move card")[0]);

    await waitFor(() => {
      expect(screen.queryByLabelText("Change this chart…")).not.toBeInTheDocument();
      expect(grid.props.isDraggable).toBe(true);
      expect(grid.props.isResizable).toBe(true);
    });
  });

  it("moves a directly displaced card into the vacated slot instead of leaving a hole", async () => {
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    const before = grid.props.layout as Array<Layout & { i: string }>;
    const dragged = before.find((item) => item.i === "card-2")!;
    (grid.props.onDragStart as (...args: unknown[]) => void)(before, dragged);
    (grid.props.onDrag as (...args: unknown[]) => void)(before, dragged);
    (grid.props.onDragStop as (...args: unknown[]) => void)([
      { i: "card-1", x: 0, y: 18, w: 6, h: 9 },
      { i: "card-2", x: 0, y: 0, w: 6, h: 9 },
    ]);

    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledWith("board-1", {
      "card-1": { x: 0, y: 9, w: 6, h: 9 },
      "card-2": { x: 0, y: 0, w: 6, h: 9 },
    }));
  });

  it("keeps neighboring cards still while a dragged card floats over them", async () => {
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    const before = grid.props.layout as Array<Layout & { i: string }>;
    const dragged = before.find((item) => item.i === "card-1")!;
    (grid.props.onDragStart as (...args: unknown[]) => void)(before, dragged);

    await waitFor(() => expect(grid.props.allowOverlap).toBe(true));

    (grid.props.onDragStop as (...args: unknown[]) => void)(before);
    await waitFor(() => expect(grid.props.allowOverlap).toBe(false));
  });

  it("uses the nearest lateral opening when an unequal displaced card cannot swap", async () => {
    cards = [
      readyCard("card-1", "Mover", { x: 0, y: 4, w: 3, h: 3 }),
      readyCard("card-2", "Wide card", { x: 4, y: 4, w: 4, h: 4 }),
      readyCard("card-3", "Origin blocker", { x: 0, y: 7, w: 4, h: 2 }),
    ];
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Mover" });

    const before = grid.props.layout as Array<Layout & { i: string }>;
    const dragged = before.find((item) => item.i === "card-1")!;
    (grid.props.onDragStart as (...args: unknown[]) => void)(before, dragged);
    (grid.props.onDrag as (...args: unknown[]) => void)(before, dragged);
    (grid.props.onDragStop as (...args: unknown[]) => void)([
      { i: "card-1", x: 4, y: 4, w: 3, h: 3 },
      { i: "card-2", x: 4, y: 4, w: 4, h: 4 },
      { i: "card-3", x: 0, y: 7, w: 4, h: 2 },
    ]);

    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledWith("board-1", {
      "card-1": { x: 4, y: 4, w: 3, h: 3 },
      "card-2": { x: 7, y: 4, w: 4, h: 4 },
      "card-3": { x: 0, y: 7, w: 4, h: 2 },
    }));
  });

  it("uses an upward opening when the drag direction and free space point upward", async () => {
    cards = [
      readyCard("card-1", "Mover", { x: 4, y: 8, w: 3, h: 3 }),
      readyCard("card-2", "Tall card", { x: 4, y: 4, w: 4, h: 4 }),
      readyCard("card-3", "Origin blocker", { x: 4, y: 11, w: 4, h: 2 }),
    ];
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Mover" });

    const before = grid.props.layout as Array<Layout & { i: string }>;
    const dragged = before.find((item) => item.i === "card-1")!;
    (grid.props.onDragStart as (...args: unknown[]) => void)(before, dragged);
    (grid.props.onDrag as (...args: unknown[]) => void)(before, dragged);
    (grid.props.onDragStop as (...args: unknown[]) => void)([
      { i: "card-1", x: 4, y: 4, w: 3, h: 3 },
      { i: "card-2", x: 4, y: 4, w: 4, h: 4 },
      { i: "card-3", x: 4, y: 11, w: 4, h: 2 },
    ]);

    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledWith("board-1", {
      "card-1": { x: 4, y: 4, w: 3, h: 3 },
      "card-2": { x: 4, y: 0, w: 4, h: 4 },
      "card-3": { x: 4, y: 11, w: 4, h: 2 },
    }));
  });

  it("never persists a card position that was displaced only by a transient panel", async () => {
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    const before = grid.props.layout as Array<Layout & { i: string }>;
    const dragged = before.find((item) => item.i === "card-1")!;
    (grid.props.onDragStart as (...args: unknown[]) => void)(before, dragged);
    (grid.props.onDrag as (...args: unknown[]) => void)(before, dragged);
    (grid.props.onDragStop as (...args: unknown[]) => void)([
      { i: "card-1", x: 0, y: 0, w: 6, h: 11 },
      { i: "card-2", x: 0, y: 11, w: 6, h: 9 },
    ]);

    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledWith("board-1", {
      "card-1": { x: 0, y: 0, w: 6, h: 9 },
      "card-2": { x: 0, y: 9, w: 6, h: 9 },
    }));
  });

  it("does not select a card through chart pointer activity", async () => {
    const user = userEvent.setup();
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    await user.click(screen.getAllByTestId("chart")[0]);

    expect(screen.queryByLabelText("Change this chart…")).not.toBeInTheDocument();
  });

  it("describes a rejected single-result chart hint as a compact KPI fallback", async () => {
    cards[0].render = {
      ...cards[0].render!,
      hint_rejected: true,
      chart_type: "big_number",
    };

    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);

    expect(await screen.findByText(
      "Requested chart wasn’t useful for this result; showing a KPI instead.",
    )).toHaveClass("chart-notice");
  });

  it("keeps SQL available and describes a valid but unplottable result honestly", async () => {
    const user = userEvent.setup();
    cards[0] = {
      ...cards[0],
      state: "broken",
      render: {
        state: "broken",
        error_reason: "unplottable",
        error: "region has 25 categories. Show a top 24, or filter this result.",
        restatement: "Sum of oil production, by region, from Daily Production.",
        row_count: 25,
        compiled_sql: "SELECT region, SUM(oil) FROM production GROUP BY 1",
        rows: [{ region: "A", oil: 10 }],
      },
    };

    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);

    expect(await screen.findByText("This result is valid, but too dense to visualize.")).toBeVisible();
    expect(screen.queryByText("This card no longer matches the semantic layer.")).not.toBeInTheDocument();
    expect(screen.getByText("25 rows")).toBeVisible();
    await user.click(screen.getByRole("heading", { name: "Oil by month" }));
    expect(await screen.findByLabelText("Change this chart…")).toBeVisible();
    await waitFor(() => {
      expect(grid.props.isDraggable).toBe(true);
      expect(grid.props.isResizable).toBe(false);
      const first = (grid.props.layout as Array<Layout & { i: string }>).find(
        (item) => item.i === "card-1",
      );
      expect(first?.h).toBeGreaterThan(9);
    });
    const sql = screen.getAllByRole("button", { name: "Compiled SQL" })[0];
    await user.click(sql);
    expect(screen.getByText(/SELECT region/)).toBeVisible();
  });

  it("supports keyboard selection and outside-click dismissal", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button type="button">Outside board</button>
        <Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />
      </div>,
    );
    const card = await screen.findByRole("article", { name: "Oil by month card" });

    fireEvent.keyDown(card, { key: "Enter" });
    expect(await screen.findByLabelText("Change this chart…")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Outside board" }));
    await waitFor(() => expect(screen.queryByLabelText("Change this chart…")).not.toBeInTheDocument());

    fireEvent.keyDown(card, { key: " " });
    expect(await screen.findByLabelText("Change this chart…")).toBeVisible();
  });

  it("keeps only one controlled SQL drawer open and closes it with Escape", async () => {
    const user = userEvent.setup();
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    const toggles = await screen.findAllByRole("button", { name: "Compiled SQL" });

    await user.click(toggles[0]);
    expect(toggles[0]).toHaveAttribute("aria-expanded", "true");
    expect(grid.props.isDraggable).toBe(true);
    expect(grid.props.isResizable).toBe(false);

    await user.click(toggles[1]);
    expect(toggles[0]).toHaveAttribute("aria-expanded", "false");
    expect(toggles[1]).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByText(/SELECT reading_date/)).toHaveLength(1);

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(toggles[1]).toHaveAttribute("aria-expanded", "false"));
    expect(grid.props.isDraggable).toBe(true);
  });

  it("releases SQL height and layout locking when a card becomes truly broken", async () => {
    const user = userEvent.setup();
    apiMocks.refreshCard.mockImplementation(async () => {
      cards[0] = {
        ...cards[0],
        state: "broken",
        render: {
          state: "broken",
          error_reason: "unknown_measure",
          error: "oil no longer exists",
        },
      };
    });
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    const sql = (await screen.findAllByRole("button", { name: "Compiled SQL" }))[0];
    await user.click(sql);
    await waitFor(() => {
      expect(grid.props.isDraggable).toBe(true);
      expect(grid.props.isResizable).toBe(false);
    });

    const firstCard = screen.getByRole("article", { name: "Oil by month card" });
    await user.click(within(firstCard).getByRole("button", { name: "Refresh data" }));

    await screen.findByText("This card no longer matches the semantic layer.");
    await waitFor(() => {
      expect(grid.props.isDraggable).toBe(true);
      const first = (grid.props.layout as Array<Layout & { i: string }>).find(
        (item) => item.i === "card-1",
      );
      expect(first?.h).toBe(9);
    });
  });

  it("removes immediately from the compact overflow menu", async () => {
    const user = userEvent.setup();
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    const card = (await screen.findByRole("heading", { name: "Oil by month" })).closest("article")!;

    await user.click(within(card).getByRole("button", { name: "Card actions" }));
    await user.click(within(card).getByRole("menuitem", { name: "Remove card" }));
    expect(apiMocks.deleteCard).toHaveBeenCalledWith("card-1");
  });

  it("creates, reveals, selects, and focuses a new empty card", async () => {
    const user = userEvent.setup();
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    apiMocks.addCard.mockImplementation(async () => {
      const created = emptyCard("card-3", { x: 6, y: 9, w: 6, h: 9 });
      cards = [...cards, created];
      return created;
    });

    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });
    await user.click(screen.getByRole("button", { name: "Add card" }));

    const input = await screen.findByLabelText("Ask for a chart…");
    await waitFor(() => expect(input).toHaveFocus());
    expect(scroll).toHaveBeenCalled();
    expect(apiMocks.addCard).toHaveBeenCalledWith("board-1");
  });

  it("places a large labeled add action in the sticky masthead", async () => {
    render(
      <>
        <header className="masthead">
          <div id="board-primary-action" />
          <h1>Semantic Dashboard</h1>
        </header>
        <Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />
      </>,
    );

    await screen.findByRole("heading", { name: "Oil by month" });
    const masthead = screen.getByRole("banner");
    const add = within(masthead).getByRole("button", { name: "Add card" });
    expect(add).toHaveTextContent("New card");
    expect(add).toHaveClass("add-card-primary");
  });

  it("places one dashboard export menu in the masthead's right-side host", async () => {
    render(
      <>
        <header className="masthead">
          <div id="board-primary-action" />
          <h1>Semantic Dashboard</h1>
          <div id="board-export-action" />
        </header>
        <Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />
      </>,
    );

    await screen.findByRole("heading", { name: "Oil by month" });
    const primary = document.getElementById("board-primary-action")!;
    const exports = document.getElementById("board-export-action")!;
    expect(within(primary).getByRole("button", { name: "Add card" })).toBeVisible();
    expect(within(primary).queryByRole("button", { name: /Export/ })).not.toBeInTheDocument();
    expect(within(exports).getByRole("button", { name: "Export Operations" })).toBeVisible();
  });

  it("appends the returned card when its post-create board reload fails", async () => {
    const user = userEvent.setup();
    const created = emptyCard("card-3", { x: 6, y: 9, w: 6, h: 9 });
    apiMocks.getBoard
      .mockResolvedValueOnce({ id: "board-1", title: "Operations", cards })
      .mockRejectedValueOnce(new Error("Board reload failed"));
    apiMocks.addCard.mockResolvedValue(created);

    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });
    await user.click(screen.getByRole("button", { name: "Add card" }));

    const input = await screen.findByLabelText("Ask for a chart…");
    await waitFor(() => expect(input).toHaveFocus());
    expect(screen.getByText("Board reload failed")).toBeVisible();
  });
});
