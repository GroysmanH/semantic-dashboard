import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Render } from "../api/types.gen";
import CardHeader from "./CardHeader";

const READY_RENDER: Render = {
  state: "ready",
  restatement: "Sum of oil production by month.",
  row_count: 2,
  rows: [{ month: "2026-01-01", oil: 12 }],
};

function setup() {
  const props = {
    title: "Oil by month",
    state: "ready" as const,
    render: READY_RENDER,
    ttlSeconds: 900,
    canUndo: false,
    busy: false,
    onSelect: vi.fn(),
    onMoveIntent: vi.fn(),
    onRefresh: vi.fn(),
    onUndo: vi.fn(),
    onRemove: vi.fn(),
    onExportPng: vi.fn(),
    onExportCsv: vi.fn(),
    onExportError: vi.fn(),
  };
  render(<CardHeader {...props} />);
  return props;
}

describe("CardHeader exports", () => {
  it("keeps PNG and CSV in a dedicated export menu", async () => {
    const user = userEvent.setup();
    const props = setup();

    const exportButton = screen.queryByRole("button", { name: "Export Oil by month" });
    expect(exportButton).not.toBeNull();
    await user.click(exportButton!);
    const exportMenu = screen.getByRole("menu", { name: "Export Oil by month" });
    expect(within(exportMenu).getByRole("menuitem", { name: "PNG" })).toBeEnabled();
    expect(within(exportMenu).getByRole("menuitem", { name: "CSV" })).toBeEnabled();

    await user.click(within(exportMenu).getByRole("menuitem", { name: "CSV" }));
    expect(props.onExportCsv).toHaveBeenCalledOnce();
  });

  it("leaves only removal in the card actions menu", async () => {
    const user = userEvent.setup();
    setup();

    await user.click(screen.getByRole("button", { name: "Card actions" }));
    const actions = screen.getByRole("menu", { name: "Card actions" });
    expect(within(actions).getAllByRole("menuitem")).toHaveLength(1);
    expect(within(actions).getByRole("menuitem", { name: "Remove card" })).toBeVisible();
  });

  it("reports a rejected export to its owning card", async () => {
    const user = userEvent.setup();
    const props = setup();
    const failure = new Error("CSV encoder rejected the rows");
    props.onExportCsv.mockRejectedValue(failure);

    await user.click(screen.getByRole("button", { name: "Export Oil by month" }));
    await user.click(screen.getByRole("menuitem", { name: "CSV" }));

    await waitFor(() => expect(props.onExportError).toHaveBeenCalledWith(failure));
  });
});
