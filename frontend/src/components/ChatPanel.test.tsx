import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ChatPanel from "./ChatPanel";
import { chatApi } from "../api/chat";

vi.mock("../api/chat", () => ({
  chatApi: {
    getThread: vi.fn(),
    sendTurn: vi.fn(),
    clearThread: vi.fn(),
    rerunTransient: vi.fn(),
    createThread: vi.fn(),
    confirmPlan: vi.fn(),
    cancelPlan: vi.fn(),
    actionProgress: vi.fn(),
    actionEvents: vi.fn(),
    stopAction: vi.fn(),
  },
}));

const CAPABILITIES = {
  anthropic: { default_model: "haiku", strong_model: "sonnet", strong_available: true },
  nvidia: { default_model: "minimax", strong_model: "minimax", strong_available: false },
};

function setup(over: Partial<Parameters<typeof ChatPanel>[0]> = {}) {
  const props = {
    threadId: "t1",
    open: true,
    pinned: false,
    width: 400,
    activeBoardId: "b1",
    activeBoardTitle: "Operations",
    boards: [{ id: "b1", title: "Operations", position: 0 }],
    provider: "anthropic" as const,
    providers: ["anthropic" as const, "nvidia" as const],
    capabilities: CAPABILITIES,
    shareVisibleData: false,
    dataSharingPermitted: true,
    selectedCardId: null,
    onClose: vi.fn(),
    onPinnedChange: vi.fn(),
    onWidthChange: vi.fn(),
    onProviderChange: vi.fn(),
    onConsentChange: vi.fn(),
    onThreadChange: vi.fn(),
    onNavigate: vi.fn(),
    onApplied: vi.fn(),
    ...over,
  };
  render(<ChatPanel {...props} />);
  return props;
}

beforeEach(() => {
  // Without this, a call recorded by an earlier test makes a later
  // "was not called" assertion pass or fail for the wrong reason.
  vi.clearAllMocks();
  vi.mocked(chatApi.getThread).mockResolvedValue(
    { id: "t1", messages: [] } as never);
});

describe("ChatPanel", () => {
  it("renders nothing while closed", () => {
    setup({ open: false });
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("says which dashboard it is looking at", async () => {
    setup();
    expect(await screen.findByText("Operations")).toBeInTheDocument();
  });

  it("sends the turn with the chosen provider and consent", async () => {
    const user = userEvent.setup();
    vi.mocked(chatApi.sendTurn).mockResolvedValue({
      message: { id: "m1", role: "assistant", action: "answer", say: "ok",
                 created_at: "now" },
    } as never);
    setup({ shareVisibleData: true });

    await user.type(screen.getByLabelText("Ask about this dashboard…"), "which region?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(chatApi.sendTurn).toHaveBeenCalled());
    expect(vi.mocked(chatApi.sendTurn).mock.calls[0][1]).toMatchObject({
      active_board_id: "b1",
      provider: "anthropic",
      share_visible_data: true,
      hard: false,
    });
  });

  it("offers no escalation for a provider whose tiers are the same model", () => {
    // A provider whose two tiers resolve to the same model id has no
    // escalation to offer, so "think harder" must not promise one.
    setup({ provider: "nvidia" });
    expect(screen.getByLabelText(/think harder/i)).toBeDisabled();
  });

  it("offers escalation when the provider has two tiers", () => {
    setup({ provider: "anthropic" });
    expect(screen.getByLabelText(/think harder/i)).toBeEnabled();
  });

  it("disables consent and says why when the server withholds it", () => {
    setup({ dataSharingPermitted: false });
    const box = screen.getByLabelText(/disabled on the server/i);
    expect(box).toBeDisabled();
    expect(box).not.toBeChecked();
  });

  it("names the provider in the consent wording", () => {
    setup({ provider: "nvidia" });
    expect(screen.getByLabelText(/NVIDIA \(free\) read the numbers/i))
      .toBeInTheDocument();
  });

  it("surfaces a failed turn in the transcript rather than a toast", async () => {
    const user = userEvent.setup();
    vi.mocked(chatApi.sendTurn).mockRejectedValue(new Error("rate limited"));
    setup();

    await user.type(screen.getByLabelText("Ask about this dashboard…"), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("rate limited")).toBeInTheDocument();
  });

  it("rotates the thread when the conversation is cleared", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(chatApi.clearThread).mockResolvedValue({ id: "t2" } as never);
    const props = setup();

    await user.click(screen.getByRole("button", { name: "Clear" }));

    await waitFor(() => expect(props.onThreadChange).toHaveBeenCalledWith("t2"));
  });

  it("keeps the conversation when clearing is declined", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const props = setup();

    await user.click(screen.getByRole("button", { name: "Clear" }));

    expect(chatApi.clearThread).not.toHaveBeenCalled();
    expect(props.onThreadChange).not.toHaveBeenCalled();
  });

  it("toggles pinning without touching any layout", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.click(screen.getByRole("button", { name: "Pin" }));
    expect(props.onPinnedChange).toHaveBeenCalledWith(true);
  });

  it("switches provider", async () => {
    const user = userEvent.setup();
    const props = setup();
    await user.selectOptions(screen.getByLabelText("Model"), "nvidia");
    expect(props.onProviderChange).toHaveBeenCalledWith("nvidia");
  });

  it("cannot send while no dashboard is open", () => {
    setup({ activeBoardId: null });
    expect(screen.getByLabelText("Ask about this dashboard…")).toBeDisabled();
  });

  // -- changes ----------------------------------------------------------

  const PLAN = {
    id: "p1",
    action: "rename_dashboard",
    say: "I will rename this tab.",
    operations: [{
      kind: "rename_dashboard",
      summary: "Rename “Operations” to “Wells”",
      before: "Operations",
      after: "Wells",
    }],
    cards: [],
    stale: false,
    created_at: "2026-08-21T00:00:00Z",
  };

  it("shows a proposed change as something to read, not something done",
     async () => {
    vi.mocked(chatApi.getThread).mockResolvedValue(
      { id: "t1", messages: [], pending_plan: PLAN } as never);
    setup();

    expect(await screen.findByText(/Rename “Operations” to “Wells”/))
      .toBeVisible();
    expect(screen.getByRole("button", { name: "Apply" })).toBeEnabled();
  });

  it("applies only what was shown, and tells the app to reload", async () => {
    const user = userEvent.setup();
    vi.mocked(chatApi.getThread).mockResolvedValue(
      { id: "t1", messages: [], pending_plan: PLAN } as never);
    vi.mocked(chatApi.confirmPlan).mockResolvedValue(
      { message: { id: "m1", role: "assistant", action: "applied",
                   say: "Renamed.", created_at: "x" },
        board_id: "b1", action: null } as never);
    const props = setup();

    await user.click(await screen.findByRole("button", { name: "Apply" }));

    expect(chatApi.confirmPlan).toHaveBeenCalledWith("p1", {
      provider: "anthropic", hard: false });
    await waitFor(() => expect(props.onApplied).toHaveBeenCalledWith("b1"));
  });

  it("discards without applying", async () => {
    const user = userEvent.setup();
    vi.mocked(chatApi.getThread).mockResolvedValue(
      { id: "t1", messages: [], pending_plan: PLAN } as never);
    vi.mocked(chatApi.cancelPlan).mockResolvedValue({} as never);
    const props = setup();

    await user.click(await screen.findByRole("button", { name: "Discard" }));

    expect(chatApi.cancelPlan).toHaveBeenCalledWith("p1");
    expect(chatApi.confirmPlan).not.toHaveBeenCalled();
    expect(props.onApplied).not.toHaveBeenCalled();
  });

  it("refuses to apply a plan the board has moved under", async () => {
    vi.mocked(chatApi.getThread).mockResolvedValue(
      { id: "t1", messages: [],
        pending_plan: { ...PLAN, stale: true } } as never);
    setup();

    expect(await screen.findByRole("button", { name: "Out of date" }))
      .toBeDisabled();
    expect(screen.getByText(/changed after this was written/)).toBeVisible();
  });

  it("follows a build that is still running", async () => {
    vi.mocked(chatApi.getThread).mockResolvedValue({
      id: "t1", messages: [],
      active_actions: [{ id: "a1", action: "new_cards", status: "running",
                         board_id: "b1", total: 3, completed: 1, failed: 0 }],
    } as never);
    setup();

    expect(await screen.findByText(/1 of 3 cards/)).toBeVisible();
    expect(screen.getByRole("button", { name: /Stop after this one/ }))
      .toBeVisible();
  });

  it("says which cards could not be built rather than hiding them",
     async () => {
    vi.mocked(chatApi.getThread).mockResolvedValue({
      id: "t1", messages: [],
      active_actions: [{ id: "a1", action: "new_cards", status: "done",
                         board_id: "b1", total: 3, completed: 2, failed: 1 }],
    } as never);
    setup();

    expect(await screen.findByText(/1 could not be built/)).toBeVisible();
    // Finished: nothing left to stop.
    expect(screen.queryByRole("button", { name: /Stop after/ }))
      .not.toBeInTheDocument();
  });
});
