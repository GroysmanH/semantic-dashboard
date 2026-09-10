import type { ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LayoutConflictError } from "../api/client";
import type { Card as CardT, Layout, LayoutMode } from "../api/client";
import Board from "./Board";

const grid = vi.hoisted(() => ({ props: {} as Record<string, unknown> }));
const apiMocks = vi.hoisted(() => ({
  getBoard: vi.fn(),
  getCard: vi.fn(),
  addCard: vi.fn(),
  duplicateCard: vi.fn(),
  saveLayout: vi.fn(),
  updateBoard: vi.fn(),
  refreshCard: vi.fn(),
  undoCard: vi.fn(),
  deleteCard: vi.fn(),
  ask: vi.fn(),
}));
const exportMocks = vi.hoisted(() => ({
  boardPng: vi.fn(),
  downloadBlob: vi.fn(),
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

vi.mock("../api/client", async (importOriginal) => ({
  ...await importOriginal<typeof import("../api/client")>(),
  api: apiMocks,
}));
vi.mock("../export/board", () => ({ boardPng: exportMocks.boardPng }));
vi.mock("../export/png", async (importOriginal) => ({
  ...await importOriginal<typeof import("../export/png")>(),
  downloadBlob: exportMocks.downloadBlob,
}));


function readyCard(id: string, title: string, layout: Layout): CardT {
  return {
    id,
    board_id: "board-1",
    title,
    semantic_query: null,
    chart_hint: null,
    state: "ready",
    can_undo: true,
    auto_size_pending: false,
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
    auto_size_pending: true,
    layout,
    ttl_seconds: 900,
    render: { state: "empty" },
  };
}

describe("Board visual-first interactions", () => {
  let cards: CardT[];
  let layoutMode: LayoutMode;

  afterEach(() => vi.restoreAllMocks());

  beforeEach(() => {
    layoutMode = "free";
    cards = [
      readyCard("card-1", "Oil by month", { x: 0, y: 0, w: 6, h: 9 }),
      readyCard("card-2", "Gas by month", { x: 0, y: 9, w: 6, h: 9 }),
    ];
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    Object.values(exportMocks).forEach((mock) => mock.mockReset());
    apiMocks.getBoard.mockImplementation(async () => ({
      id: "board-1",
      title: "Operations",
      position: 0,
      layout_mode: layoutMode,
      revision: 1,
      cards,
    }));
    apiMocks.getCard.mockImplementation(async (id: string) => cards.find((card) => card.id === id));
    apiMocks.saveLayout.mockImplementation(async (_boardId, layouts) => ({
      layouts,
      revision: 2,
    }));
    apiMocks.updateBoard.mockImplementation(async (_id, fields) => {
      layoutMode = fields.layout_mode ?? layoutMode;
      return {
        id: "board-1",
        title: "Operations",
        position: 0,
        layout_mode: layoutMode,
        revision: 2,
      };
    });
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

  it("keeps board-snapshot geometry when hydrated card data carries a stale layout", async () => {
    const snapshot = [
      readyCard("card-1", "Oil by month", { x: 6, y: 7, w: 6, h: 9 }),
      readyCard("card-2", "Gas by month", { x: 0, y: 20, w: 6, h: 9 }),
    ];
    apiMocks.getBoard.mockResolvedValue({
      id: "board-1", title: "Operations", position: 0,
      layout_mode: "free", schema_name: "ddh", revision: 6, cards: snapshot,
    });
    apiMocks.getCard.mockImplementation(async (id: string) => ({
      ...snapshot.find((card) => card.id === id)!,
      layout: { x: 0, y: 0, w: 3, h: 6 },
    }));

    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    expect((grid.props.layout as Array<Layout & { i: string }>).map(
      ({ i, x, y, w, h }) => ({ i, x, y, w, h }),
    )).toEqual([
      { i: "card-1", x: 6, y: 7, w: 6, h: 9 },
      { i: "card-2", x: 0, y: 20, w: 6, h: 9 },
    ]);
  });

  it("renders usable board card data when one per-card hydration request fails", async () => {
    apiMocks.getCard
      .mockRejectedValueOnce(new Error("card hydration failed"))
      .mockResolvedValueOnce(cards[1]);

    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);

    expect(await screen.findByRole("heading", { name: "Oil by month" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Gas by month" })).toBeVisible();
    expect(screen.queryByText("card hydration failed")).not.toBeInTheDocument();
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
    }, [], expect.any(Number)));
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
    }, [], expect.any(Number)));
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
    }, [], expect.any(Number)));
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
    }, [], expect.any(Number)));
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
    }, [], expect.any(Number)));
  });

  it("reconciles an optimistic drag to the canonical layout returned by the server", async () => {
    let release!: (value: { layouts: Record<string, Layout>; revision: number }) => void;
    apiMocks.saveLayout.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    const proposed = [
      { i: "card-1", x: 6, y: 1, w: 6, h: 9 },
      { i: "card-2", x: 0, y: 9, w: 6, h: 9 },
    ];
    act(() => (grid.props.onDragStop as (layout: typeof proposed) => void)(proposed));
    await waitFor(() => expect(
      (grid.props.layout as Array<Layout & { i: string }>).find((item) => item.i === "card-1"),
    ).toMatchObject({ x: 6, y: 1 }));

    await act(async () => release({
      layouts: {
        "card-1": { x: 6, y: 0, w: 6, h: 9 },
        "card-2": { x: 0, y: 9, w: 6, h: 9 },
      },
      revision: 8,
    }));
    await waitFor(() => expect(
      (grid.props.layout as Array<Layout & { i: string }>).find((item) => item.i === "card-1"),
    ).toMatchObject({ x: 6, y: 0 }));
  });

  it("preserves newer unrelated geometry when authoritative layout changes during Auto-pack drag", async () => {
    const user = userEvent.setup();
    layoutMode = "auto_pack";
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });
    const started = grid.props.layout as Array<Layout & { i: string }>;
    const active = started.find((item) => item.i === "card-1")!;
    act(() => (grid.props.onDragStart as (...args: unknown[]) => void)(started, active));

    apiMocks.refreshCard.mockImplementationOnce(async () => {
      cards = [
        { ...cards[0], layout: { x: 0, y: 0, w: 6, h: 9 } },
        { ...cards[1], layout: { x: 6, y: 4, w: 6, h: 9 } },
      ];
      apiMocks.getBoard.mockResolvedValueOnce({
        id: "board-1", title: "Operations", position: 0,
        layout_mode: "auto_pack", schema_name: "ddh", revision: 5, cards,
      });
    });
    const source = screen.getByRole("article", { name: "Oil by month card" });
    await user.click(within(source).getByRole("button", { name: "Refresh data" }));
    await waitFor(() => expect(
      (grid.props.layout as Array<Layout & { i: string }>).find((item) => item.i === "card-2"),
    ).toMatchObject({ x: 6, y: 4 }));

    act(() => (grid.props.onDragStop as (...args: unknown[]) => void)([
      { i: "card-1", x: 0, y: 10, w: 6, h: 9 },
      { i: "card-2", x: 0, y: 9, w: 6, h: 9 },
    ]));

    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledWith("board-1", {
      "card-1": { x: 0, y: 0, w: 6, h: 9 },
      "card-2": { x: 6, y: 0, w: 6, h: 9 },
    }, [], expect.any(Number)));
  });

  it("preserves newer positions while applying only the active resize size", async () => {
    const user = userEvent.setup();
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });
    const started = grid.props.layout as Array<Layout & { i: string }>;
    act(() => (grid.props.onResizeStart as (...args: unknown[]) => void)(started, started[0], started[0]));

    apiMocks.refreshCard.mockImplementationOnce(async () => {
      cards = [
        { ...cards[0], layout: { x: 2, y: 3, w: 6, h: 9 } },
        { ...cards[1], layout: { x: 6, y: 20, w: 6, h: 9 } },
      ];
      apiMocks.getBoard.mockResolvedValueOnce({
        id: "board-1", title: "Operations", position: 0,
        layout_mode: "free", schema_name: "ddh", revision: 5, cards,
      });
    });
    const source = screen.getByRole("article", { name: "Oil by month card" });
    await user.click(within(source).getByRole("button", { name: "Refresh data" }));
    await waitFor(() => expect(
      (grid.props.layout as Array<Layout & { i: string }>).find((item) => item.i === "card-2"),
    ).toMatchObject({ x: 6, y: 20 }));

    act(() => (grid.props.onResizeStop as (...args: unknown[]) => void)([
      { i: "card-1", x: 0, y: 0, w: 7, h: 10 },
      { i: "card-2", x: 0, y: 9, w: 6, h: 9 },
    ]));

    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledWith(
      "board-1",
      {
        "card-1": { x: 2, y: 3, w: 7, h: 10 },
        "card-2": { x: 6, y: 20, w: 6, h: 9 },
      },
      ["card-1"],
      expect.any(Number),
    ));
  });

  it("marks only the actively resized card as manually resized", async () => {
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });
    const next = [
      { i: "card-1", x: 0, y: 0, w: 7, h: 10 },
      { i: "card-2", x: 0, y: 10, w: 6, h: 9 },
    ];

    act(() => {
      (grid.props.onResizeStart as (...args: unknown[]) => void)(next, next[0], next[0]);
      (grid.props.onResizeStop as (layout: typeof next) => void)(next);
    });

    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledWith(
      "board-1",
      {
        "card-1": { x: 0, y: 0, w: 7, h: 10 },
        "card-2": { x: 0, y: 9, w: 6, h: 9 },
      },
      ["card-1"],
      expect.any(Number),
    ));
  });

  it("packs upward immediately when Auto-pack is enabled, then accepts server geometry", async () => {
    const user = userEvent.setup();
    cards = [
      readyCard("card-1", "Oil by month", { x: 0, y: 8, w: 6, h: 4 }),
      readyCard("card-2", "Gas by month", { x: 0, y: 20, w: 6, h: 5 }),
    ];
    let release!: () => void;
    apiMocks.updateBoard.mockReturnValue(new Promise((resolve) => {
      release = () => {
        cards = [
          { ...cards[0], layout: { x: 0, y: 1, w: 6, h: 4 } },
          { ...cards[1], layout: { x: 0, y: 5, w: 6, h: 5 } },
        ];
        resolve({
          id: "board-1", title: "Operations", position: 0,
          layout_mode: "auto_pack", schema_name: "ddh", revision: 2,
        });
      };
    }));
    apiMocks.getBoard
      .mockResolvedValueOnce({
        id: "board-1", title: "Operations", position: 0,
        layout_mode: "free", schema_name: "ddh", revision: 1, cards,
      })
      .mockImplementation(async () => ({
        id: "board-1", title: "Operations", position: 0,
        layout_mode: "auto_pack", schema_name: "ddh", revision: 2,
        cards: [
          { ...cards[0], layout: { x: 0, y: 1, w: 6, h: 4 } },
          { ...cards[1], layout: { x: 0, y: 5, w: 6, h: 5 } },
        ],
      }));

    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    const toggle = await screen.findByRole("switch", { name: "Auto-pack" });
    await user.click(toggle);

    await waitFor(() => {
      const packed = grid.props.layout as Array<Layout & { i: string }>;
      expect(packed.map(({ i, x, y, w, h }) => ({ i, x, y, w, h }))).toEqual([
        { i: "card-1", x: 0, y: 0, w: 6, h: 4 },
        { i: "card-2", x: 0, y: 4, w: 6, h: 5 },
      ]);
      expect(screen.getByRole("status")).toHaveTextContent("Auto-pack on");
    });
    expect(apiMocks.updateBoard).toHaveBeenCalledWith("board-1", { layout_mode: "auto_pack" });

    await act(async () => release());
    await waitFor(() => {
      const canonical = grid.props.layout as Array<Layout & { i: string }>;
      expect(canonical.find((item) => item.i === "card-1")).toMatchObject({ y: 1 });
      expect(canonical.find((item) => item.i === "card-2")).toMatchObject({ y: 5 });
    });
  });

  it("persists Auto-pack independently per dashboard and supports keyboard toggling", async () => {
    const user = userEvent.setup();
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    const toggle = await screen.findByRole("switch", { name: "Auto-pack" });

    toggle.focus();
    await user.keyboard(" ");

    expect(toggle).toBeChecked();
    expect(apiMocks.updateBoard).toHaveBeenCalledWith("board-1", { layout_mode: "auto_pack" });
    expect(screen.getByRole("status")).toHaveTextContent("Auto-pack on");
  });

  it("locks layout gestures and does not roll back newer geometry when Auto-pack fails", async () => {
    const user = userEvent.setup();
    let resolveLayout!: (result: { layouts: Record<string, Layout>; revision: number }) => void;
    let rejectMode!: (error: Error) => void;
    apiMocks.saveLayout.mockReturnValue(new Promise((resolve) => { resolveLayout = resolve; }));
    apiMocks.updateBoard.mockReturnValue(new Promise((_resolve, reject) => { rejectMode = reject; }));
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    act(() => (grid.props.onDragStop as (...args: unknown[]) => void)([
      { i: "card-1", x: 3, y: 4, w: 6, h: 9 },
      { i: "card-2", x: 0, y: 13, w: 6, h: 9 },
    ]));
    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledOnce());
    await user.click(screen.getByRole("switch", { name: "Auto-pack" }));
    await waitFor(() => {
      expect(grid.props.isDraggable).toBe(false);
      expect(grid.props.isResizable).toBe(false);
    });

    const latest: Record<string, Layout> = {
      "card-1": { x: 6, y: 2, w: 6, h: 9 },
      "card-2": { x: 0, y: 20, w: 6, h: 9 },
    };
    cards = cards.map((card) => ({ ...card, layout: latest[card.id] }));
    apiMocks.getBoard.mockResolvedValueOnce({
      id: "board-1", title: "Operations", position: 0,
      layout_mode: "free", schema_name: "ddh", revision: 8, cards,
    });
    await act(async () => resolveLayout({ layouts: latest, revision: 8 }));
    await act(async () => rejectMode(new Error("Auto-pack rejected")));

    await waitFor(() => {
      const current = grid.props.layout as Array<Layout & { i: string }>;
      expect(current.find((item) => item.i === "card-1")).toMatchObject(latest["card-1"]);
      expect(current.find((item) => item.i === "card-2")).toMatchObject(latest["card-2"]);
      expect(screen.getByRole("switch", { name: "Auto-pack" })).not.toBeChecked();
    });
  });

  it("reapplies only the active drag once after a revision conflict", async () => {
    const serverAfterOtherClient: Record<string, Layout> = {
      "card-1": { x: 0, y: 0, w: 6, h: 9 },
      "card-2": { x: 6, y: 4, w: 6, h: 9 },
    };
    const final: Record<string, Layout> = {
      "card-1": { x: 6, y: 20, w: 6, h: 9 },
      "card-2": serverAfterOtherClient["card-2"],
    };
    apiMocks.saveLayout
      .mockRejectedValueOnce(new LayoutConflictError({
        code: "layout_conflict",
        message: "This dashboard changed while you were arranging it.",
        layouts: serverAfterOtherClient,
        revision: 2,
      }))
      .mockResolvedValueOnce({ layouts: final, revision: 3 });
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });
    const before = grid.props.layout as Array<Layout & { i: string }>;
    const dragged = before.find((item) => item.i === "card-1")!;

    act(() => {
      (grid.props.onDragStart as (...args: unknown[]) => void)(before, dragged);
      (grid.props.onDrag as (...args: unknown[]) => void)(before, dragged);
      (grid.props.onDragStop as (...args: unknown[]) => void)([
        { i: "card-1", x: 6, y: 20, w: 6, h: 9 },
        { i: "card-2", x: 0, y: 9, w: 6, h: 9 },
      ]);
    });

    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledTimes(2));
    expect(apiMocks.saveLayout).toHaveBeenNthCalledWith(
      1,
      "board-1",
      expect.any(Object),
      [],
      1,
    );
    expect(apiMocks.saveLayout).toHaveBeenNthCalledWith(
      2,
      "board-1",
      final,
      [],
      2,
    );
    await waitFor(() => expect(
      (grid.props.layout as Array<Layout & { i: string }>).map(
        ({ i, x, y, w, h }) => [i, { x, y, w, h }],
      ),
    ).toEqual(Object.entries(final)));
    expect(screen.queryByText(/changed while you were arranging/i))
      .not.toBeInTheDocument();
  });

  it("uses an upward-only packed preview after dragging in Auto-pack mode", async () => {
    layoutMode = "auto_pack";
    cards = [
      readyCard("card-1", "Oil by month", { x: 0, y: 0, w: 6, h: 9 }),
      readyCard("card-2", "Gas by month", { x: 6, y: 0, w: 6, h: 9 }),
    ];
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });
    const before = grid.props.layout as Array<Layout & { i: string }>;
    const dragged = before.find((item) => item.i === "card-2")!;

    act(() => {
      (grid.props.onDragStart as (...args: unknown[]) => void)(before, dragged);
      (grid.props.onDrag as (...args: unknown[]) => void)(before, dragged);
      (grid.props.onDragStop as (...args: unknown[]) => void)([
        { i: "card-1", x: 0, y: 0, w: 6, h: 9 },
        { i: "card-2", x: 6, y: 14, w: 6, h: 9 },
      ]);
    });

    await waitFor(() => expect(apiMocks.saveLayout).toHaveBeenCalledWith("board-1", {
      "card-1": { x: 0, y: 0, w: 6, h: 9 },
      "card-2": { x: 6, y: 0, w: 6, h: 9 },
    }, [], expect.any(Number)));
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

    // The sentence sits in a span now, because the note carries its own
    // dismiss control alongside it.
    const said = await screen.findByText(
      "Requested chart wasn’t useful for this result; showing a KPI instead.",
    );
    expect(said.closest("div")).toHaveClass("chart-notice");
  });

  it("lets the chart-fallback note be dismissed, and remembers it", async () => {
    const user = userEvent.setup();
    cards[0].render = {
      ...cards[0].render!,
      hint_rejected: true,
      chart_type: "big_number",
    };

    const { unmount } = render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);

    await user.click(await screen.findByRole("button", { name: "Hide this note" }));
    expect(screen.queryByText(/Requested chart wasn/)).not.toBeInTheDocument();

    // It is a preference, not component state: reopening the board must
    // not re-announce a decision the person has already dismissed.
    unmount();
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByText("Oil by month");
    expect(screen.queryByText(/Requested chart wasn/)).not.toBeInTheDocument();
  });

  it("re-announces when a different chart is substituted", async () => {
    const user = userEvent.setup();
    cards[0].render = {
      ...cards[0].render!,
      hint_rejected: true,
      chart_type: "big_number",
    };

    const { unmount } = render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await user.click(await screen.findByRole("button", { name: "Hide this note" }));
    unmount();

    // A different override is a different thing to have been told, and
    // dismissing the first one must not silence it.
    cards[0].render = { ...cards[0].render!, chart_type: "line" };
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);

    expect(await screen.findByText(
      "Requested chart wasn’t useful for this result; showing a line chart instead.",
    )).toBeVisible();
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

  it("duplicates a ready card, applies canonical layouts, hydrates, and focuses the copy", async () => {
    const user = userEvent.setup();
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    const copied = readyCard("card-3", "Copy of Oil by month", { x: 6, y: 0, w: 6, h: 9 });
    apiMocks.duplicateCard.mockImplementation(async () => {
      cards = [...cards, copied];
      return {
        card: copied,
        layouts: {
          "card-1": { x: 0, y: 0, w: 6, h: 9 },
          "card-2": { x: 0, y: 9, w: 6, h: 9 },
          "card-3": { x: 6, y: 0, w: 6, h: 9 },
        },
        revision: 3,
      };
    });

    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    const source = (await screen.findByRole("heading", { name: "Oil by month" })).closest("article")!;
    await user.click(within(source).getByRole("button", { name: "Card actions" }));
    await user.click(within(source).getByRole("menuitem", { name: "Duplicate card" }));

    const copy = await screen.findByRole("article", { name: "Copy of Oil by month card" });
    await waitFor(() => expect(copy).toHaveFocus());
    expect(within(copy).queryByLabelText("Ask for a chart…")).not.toBeInTheDocument();
    expect(copy).toHaveAttribute("aria-selected", "true");
    expect(scroll).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
    expect(apiMocks.getCard).toHaveBeenCalledWith("card-3");
    expect(apiMocks.duplicateCard).toHaveBeenCalledWith("card-1");
  });

  it("shows duplication busy and error state on the source card", async () => {
    const user = userEvent.setup();
    let reject!: (error: Error) => void;
    apiMocks.duplicateCard.mockReturnValue(new Promise((_resolve, rejectPromise) => {
      reject = rejectPromise;
    }));
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    const source = (await screen.findByRole("heading", { name: "Oil by month" })).closest("article")!;
    await user.click(within(source).getByRole("button", { name: "Card actions" }));
    await user.click(within(source).getByRole("menuitem", { name: "Duplicate card" }));

    await waitFor(() => {
      expect(source).toHaveAttribute("aria-busy", "true");
      expect(within(source).getByRole("menuitem", { name: "Duplicating…" })).toBeDisabled();
    });
    await act(async () => reject(new Error("Duplicate failed")));

    expect(await within(source).findByText("Duplicate failed")).toBeVisible();
    expect(source).toHaveAttribute("aria-busy", "false");
  });

  it("avoids smooth scrolling when reduced motion is requested", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    const copied = readyCard("card-3", "Copy of Oil by month", { x: 6, y: 0, w: 6, h: 9 });
    apiMocks.duplicateCard.mockImplementation(async () => {
      cards = [...cards, copied];
      return {
        card: copied,
        layouts: Object.fromEntries(cards.map((card) => [card.id, card.layout])),
        revision: 3,
      };
    });
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    const source = (await screen.findByRole("heading", { name: "Oil by month" })).closest("article")!;
    await user.click(within(source).getByRole("button", { name: "Card actions" }));
    await user.click(within(source).getByRole("menuitem", { name: "Duplicate card" }));

    await screen.findByRole("article", { name: "Copy of Oil by month card" });
    await waitFor(() => expect(scroll).toHaveBeenCalledWith({ behavior: "auto", block: "center" }));
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
    const primary = document.getElementById("board-primary-action")!;
    expect(within(primary).queryByRole("switch", { name: "Auto-pack" })).not.toBeInTheDocument();
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
    const autoPack = within(exports).getByRole("switch", { name: "Auto-pack" });
    expect(autoPack).toBeVisible();
    const exportButton = within(exports).getByRole("button", { name: "Export Operations" });
    expect(exportButton).toBeVisible();
    const autoPackControl = autoPack.closest(".auto-pack-control");
    expect(autoPackControl).not.toBeNull();
    expect(autoPackControl!.compareDocumentPosition(exportButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("passes a hydrated snapshot for every ready chart and waits before downloading", async () => {
    const user = userEvent.setup();
    let finish!: (blob: Blob) => void;
    exportMocks.boardPng.mockReturnValue(new Promise<Blob>((resolve) => { finish = resolve; }));
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    await user.click(screen.getByRole("button", { name: "Export Operations" }));
    await user.click(screen.getByRole("menuitem", { name: "PNG" }));

    await waitFor(() => expect(exportMocks.boardPng).toHaveBeenCalled());
    const entries = exportMocks.boardPng.mock.calls[0][1] as Array<{ snapshot: unknown }>;
    expect(entries).toHaveLength(2);
    expect(entries.every((entry) => typeof entry.snapshot === "function")).toBe(true);
    expect(exportMocks.downloadBlob).not.toHaveBeenCalled();

    const blob = new Blob(["dashboard"]);
    finish(blob);
    await waitFor(() => expect(exportMocks.downloadBlob).toHaveBeenCalledWith(
      blob,
      expect.stringMatching(/^operations-.*\.png$/),
    ));
  });

  it("shows named dashboard snapshot failures without starting a download", async () => {
    const user = userEvent.setup();
    exportMocks.boardPng.mockRejectedValue(new Error(
      "Could not export Gas by month. No dashboard image was downloaded.",
    ));
    render(<Board boardId="board-1" examples={[]} provider="anthropic" providers={["anthropic"]} onProviderChange={() => {}} strongAvailable />);
    await screen.findByRole("heading", { name: "Oil by month" });

    await user.click(screen.getByRole("button", { name: "Export Operations" }));
    await user.click(screen.getByRole("menuitem", { name: "PNG" }));

    expect(await screen.findByText(
      "Could not export Gas by month. No dashboard image was downloaded.",
    )).toBeVisible();
    expect(exportMocks.downloadBlob).not.toHaveBeenCalled();
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
