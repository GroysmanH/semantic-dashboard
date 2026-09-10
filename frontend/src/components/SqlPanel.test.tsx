import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import SqlPanel from "./SqlPanel";

it("exposes controlled accordion state and reports only the open drawer height", async () => {
  const user = userEvent.setup();
  const onToggle = vi.fn();
  const onHeightChange = vi.fn();
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 240,
    width: 0, height: 240, toJSON: () => ({}),
  } as DOMRect);

  const view = render(
    <SqlPanel
      sql="SELECT 1"
      open={false}
      onToggle={onToggle}
      onHeightChange={onHeightChange}
    />,
  );
  const toggle = screen.getByRole("button", { name: "Compiled SQL" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("SELECT 1")).not.toBeInTheDocument();

  await user.click(toggle);
  expect(onToggle).toHaveBeenCalledOnce();

  view.rerender(
    <SqlPanel
      sql="SELECT 1"
      open
      onToggle={onToggle}
      onHeightChange={onHeightChange}
    />,
  );
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByText("SELECT 1")).toBeVisible();
  await waitFor(() => expect(onHeightChange).toHaveBeenLastCalledWith(240));

  view.rerender(
    <SqlPanel
      sql="SELECT 1"
      open={false}
      onToggle={onToggle}
      onHeightChange={onHeightChange}
    />,
  );
  expect(onHeightChange).toHaveBeenLastCalledWith(0);
});
