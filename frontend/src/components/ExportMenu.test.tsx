import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ExportMenu from "./ExportMenu";

function setup() {
  const first = vi.fn();
  const middle = vi.fn();
  const last = vi.fn();
  render(
    <>
      <ExportMenu
        label="Export Operations"
        items={[
          { label: "Unavailable", disabled: true, run: first },
          { label: "PNG", run: middle },
          { label: "JSON", run: last },
        ]}
      />
      <button type="button">Outside target</button>
    </>,
  );
  return { first, middle, last };
}

describe("ExportMenu keyboard behavior", () => {
  it("focuses the first enabled item when opened", async () => {
    const user = userEvent.setup();
    setup();

    await user.click(screen.getByRole("button", { name: "Export Operations" }));

    await waitFor(() => expect(screen.getByRole("menuitem", { name: "PNG" })).toHaveFocus());
  });

  it("moves among enabled items with arrows, Home, and End", async () => {
    const user = userEvent.setup();
    setup();
    await user.click(screen.getByRole("button", { name: "Export Operations" }));
    const png = screen.getByRole("menuitem", { name: "PNG" });
    const json = screen.getByRole("menuitem", { name: "JSON" });
    await waitFor(() => expect(png).toHaveFocus());

    await user.keyboard("{ArrowDown}");
    expect(json).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(png).toHaveFocus();
    await user.keyboard("{End}");
    expect(json).toHaveFocus();
    await user.keyboard("{Home}");
    expect(png).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(json).toHaveFocus();
  });

  it("returns focus on Escape and leaves outside-click focus alone", async () => {
    const user = userEvent.setup();
    setup();
    const trigger = screen.getByRole("button", { name: "Export Operations" });
    await user.click(trigger);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    await user.click(trigger);
    const outside = screen.getByRole("button", { name: "Outside target" });
    await user.click(outside);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(outside).toHaveFocus();
  });
});
