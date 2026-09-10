import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const apiMocks = vi.hoisted(() => ({
  listBoards: vi.fn(),
  createBoard: vi.fn(),
  updateBoard: vi.fn(),
  reorderBoards: vi.fn(),
  deleteBoard: vi.fn(),
  getBoard: vi.fn(),
  getCard: vi.fn(),
  layer: vi.fn(),
  schemas: vi.fn(() => Promise.resolve([])),
}));

const chatApiMocks = vi.hoisted(() => ({
  getThread: vi.fn(),
  createThread: vi.fn(),
}));

vi.mock("./api/client", () => ({ api: apiMocks }));
vi.mock("./api/chat", () => ({ chatApi: chatApiMocks }));

const boardTitles = () => within(screen.getByRole("tablist"))
  .getAllByRole("tab")
  .map((tab) => tab.textContent);

describe("dashboard ordering", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.listBoards.mockResolvedValue([
      { id: "a", title: "Operations", position: 0 },
      { id: "b", title: "Drilling", position: 1 },
      { id: "c", title: "Finance", position: 2 },
    ]);
    apiMocks.layer.mockResolvedValue({
      entities: [],
      examples: [],
      providers: { default: "gemini", available: ["gemini"] },
    });
  });

  it("shows the optimistic order and rolls it back when persistence fails", async () => {
    const user = userEvent.setup();
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const text = this.textContent ?? "";
      const index = text.includes("Operations") ? 0 : text.includes("Drilling") ? 1 : 2;
      const left = index * 120;
      return {
        x: left, y: 0, top: 0, left, right: left + 110, bottom: 40,
        width: 110, height: 40, toJSON: () => ({}),
      } as DOMRect;
    });
    let rejectReorder!: (reason: Error) => void;
    apiMocks.reorderBoards.mockReturnValue(new Promise<void>((_resolve, reject) => {
      rejectReorder = reject;
    }));

    render(<App />);
    await screen.findByRole("tab", { name: "Operations" });
    const handle = screen.queryByRole("button", { name: "Reorder Operations" });
    expect(handle).not.toBeNull();
    handle?.focus();
    await user.keyboard("{Enter}{ArrowRight}{Enter}");

    expect(boardTitles()).toEqual(["Drilling", "Operations", "Finance"]);
    rejectReorder(new Error("Could not save dashboard order"));
    await waitFor(() => expect(boardTitles()).toEqual(["Operations", "Drilling", "Finance"]));
    expect(screen.getByText("Could not save dashboard order")).toBeVisible();
  });

  it("prevents a rename from racing a pending reorder and preserves current board fields on rollback", async () => {
    const user = userEvent.setup();
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const text = this.textContent ?? "";
      const index = text.includes("Operations") ? 0 : text.includes("Drilling") ? 1 : 2;
      const left = index * 120;
      return {
        x: left, y: 0, top: 0, left, right: left + 110, bottom: 40,
        width: 110, height: 40, toJSON: () => ({}),
      } as DOMRect;
    });
    let rejectReorder!: (reason: Error) => void;
    apiMocks.reorderBoards.mockReturnValue(new Promise<void>((_resolve, reject) => {
      rejectReorder = reject;
    }));

    render(<App />);
    await screen.findByRole("tab", { name: "Operations" });
    const handle = screen.getByRole("button", { name: "Reorder Operations" });
    handle.focus();
    await user.keyboard("{Enter}{ArrowRight}{Enter}");

    await user.dblClick(screen.getByRole("tab", { name: "Operations" }));
    expect(screen.queryByRole("textbox", { name: "Rename Operations" })).not.toBeInTheDocument();
    expect(apiMocks.updateBoard).not.toHaveBeenCalled();

    rejectReorder(new Error("Could not save dashboard order"));
    await waitFor(() => expect(boardTitles()).toEqual(["Operations", "Drilling", "Finance"]));
  });
});

describe("chat recovery navigation", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    Object.values(chatApiMocks).forEach((mock) => mock.mockReset());
    localStorage.setItem("semantic-dashboard:last-board", "b2");
    localStorage.setItem("semantic-dashboard:chat-thread", "t1");
    localStorage.setItem("semantic-dashboard:chat-open", "true");
    apiMocks.listBoards.mockResolvedValue([
      { id: "b1", title: "Operations", position: 0,
        layout_mode: "free", schema_name: "ddh", revision: 1 },
      { id: "b2", title: "Drilling", position: 1,
        layout_mode: "free", schema_name: "ddh", revision: 1 },
    ]);
    apiMocks.layer.mockResolvedValue({
      entities: [], examples: [],
      providers: {
        default: "gemini", available: ["gemini"],
        capabilities: {
          gemini: { default_model: "flash", strong_model: "pro",
                    strong_available: true },
        },
      },
      chat: { enabled: true, data_sharing_permitted: false },
    });
    apiMocks.getBoard.mockImplementation(async (id: string) => ({
      id,
      title: id === "b1" ? "Operations" : "Drilling",
      position: id === "b1" ? 0 : 1,
      layout_mode: "free", schema_name: "ddh",
      revision: 1,
      cards: id === "b1" ? [{
        id: "c1", board_id: "b1", title: "Oil by region",
        semantic_query: { entity: "production", measures: ["oil"],
                          dimensions: [{ field: "region" }] },
        chart_hint: null, state: "broken", can_undo: false,
        auto_size_pending: false,
        layout: { x: 0, y: 0, w: 6, h: 10 }, ttl_seconds: 900,
        render: { state: "broken", error_reason: "unplottable" },
      }] : [],
    }));
    apiMocks.getCard.mockImplementation(async () => (
      await apiMocks.getBoard("b1")
    ).cards[0]);
    chatApiMocks.getThread.mockResolvedValue({
      id: "t1",
      messages: [{
        id: "m1", role: "assistant", action: "refuse",
        refusal: "I couldn’t prepare that change safely. Nothing changed.",
        recovery: {
          retryable: true, retry_text: "show it by month", target_card_id: "c1",
        },
        active_board_id: "b1", active_board_title: "Operations",
        created_at: "now",
      }],
    });
  });

  it("opens and focuses the recovered card's edit strip on its dashboard", async () => {
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByRole("tab", { name: "Drilling" }))
      .toHaveAttribute("aria-selected", "true");

    await user.click(await screen.findByRole("button", { name: "Edit card" }));

    expect(await screen.findByRole("tab", { name: "Operations" }))
      .toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("article", { name: "Oil by region card" }))
      .toHaveAttribute("aria-selected", "true");
    const input = await screen.findByLabelText("Change this chart…");
    expect(input).toBeVisible();
    await waitFor(() => expect(input).toHaveFocus());
  });

  it("reveals a source card without opening its edit strip", async () => {
    const user = userEvent.setup();
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    chatApiMocks.getThread.mockResolvedValue({
      id: "t1",
      messages: [{
        id: "m-source", role: "assistant", action: "answer",
        say: "Operations leads.",
        claims: [{
          text: "Operations leads.", displayed_value: "Operations",
          sources: [{
            board_id: "b1", card_id: "c1", card_title: "Oil by region",
          }],
        }],
        active_board_id: "b1", active_board_title: "Operations",
        created_at: "now",
      }],
    });
    render(<App />);
    expect(await screen.findByRole("tab", { name: "Drilling" }))
      .toHaveAttribute("aria-selected", "true");

    await user.click(await screen.findByRole("button", { name: "Oil by region" }));

    expect(await screen.findByRole("tab", { name: "Operations" }))
      .toHaveAttribute("aria-selected", "true");
    const card = await screen.findByRole("article", { name: "Oil by region card" });
    expect(card).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByLabelText("Change this chart…")).not.toBeInTheDocument();
    await waitFor(() => expect(card).toHaveFocus());
    expect(scroll).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
  });
});
