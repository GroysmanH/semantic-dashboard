import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import TabBar from "./TabBar";
import type { BoardSummary } from "../api/client";

const BOARDS: BoardSummary[] = [
  { id: "a", title: "Operations", position: 0 },
  { id: "b", title: "Drilling", position: 1 },
];

function setup(overrides: Partial<Parameters<typeof TabBar>[0]> = {}) {
  const props = {
    boards: BOARDS,
    activeId: "a",
    busy: false,
    onSelect: vi.fn(),
    onCreate: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    ...overrides,
  };
  render(<TabBar {...props} />);
  return props;
}

describe("TabBar", () => {
  it("marks only the active board as selected", () => {
    setup();
    expect(screen.getByRole("tab", { name: "Operations" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByRole("tab", { name: "Drilling" })).toHaveAttribute(
      "aria-selected",
      "false",
    );
  });

  it("selects a board when its tab is clicked", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole("tab", { name: "Drilling" }));
    expect(props.onSelect).toHaveBeenCalledWith("b");
  });

  it("renames on double click and commits with Enter", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.dblClick(screen.getByRole("tab", { name: "Operations" }));

    const input = screen.getByLabelText("Rename Operations");
    await user.clear(input);
    await user.type(input, "Production{Enter}");

    expect(props.onRename).toHaveBeenCalledWith("a", "Production");
  });

  it("abandons a rename on Escape", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.dblClick(screen.getByRole("tab", { name: "Operations" }));

    const input = screen.getByLabelText("Rename Operations");
    await user.clear(input);
    await user.type(input, "Discarded{Escape}");

    expect(props.onRename).not.toHaveBeenCalled();
    expect(screen.getByRole("tab", { name: "Operations" })).toBeInTheDocument();
  });

  it("keeps the old name when the draft is emptied", async () => {
    // An empty title would render a tab with nothing to click.
    const user = userEvent.setup();
    const props = setup();
    await user.dblClick(screen.getByRole("tab", { name: "Operations" }));

    const input = screen.getByLabelText("Rename Operations");
    await user.clear(input);
    await user.type(input, "{Enter}");

    expect(props.onRename).not.toHaveBeenCalled();
  });

  it("does not rename when the title is unchanged", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.dblClick(screen.getByRole("tab", { name: "Operations" }));
    await user.type(screen.getByLabelText("Rename Operations"), "{Enter}");
    expect(props.onRename).not.toHaveBeenCalled();
  });

  it("creates a board", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole("button", { name: "New dashboard" }));
    expect(props.onCreate).toHaveBeenCalledOnce();
  });

  it("deletes a board", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole("button", { name: "Delete Drilling" }));
    expect(props.onDelete).toHaveBeenCalledWith("b");
  });

  it("offers no delete when only one board is left", () => {
    // Deleting the last tab would leave the app with nothing to render.
    setup({ boards: [BOARDS[0]], activeId: "a" });
    expect(
      screen.getByRole("button", { name: "Delete Operations" }),
    ).toBeDisabled();
  });

  it("disables creation and deletion while a board mutation is in flight", () => {
    setup({ busy: true });
    expect(screen.getByRole("button", { name: "New dashboard" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Delete Drilling" })).toBeDisabled();
  });
});
