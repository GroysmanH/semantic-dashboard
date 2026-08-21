import type { ReactNode } from "react";
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
  layer: vi.fn(),
}));

vi.mock("./api/client", () => ({ api: apiMocks }));
vi.mock("./components/Board", () => ({ default: (_props: unknown): ReactNode => null }));

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
