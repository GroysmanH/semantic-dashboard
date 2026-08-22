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
    undoAction: vi.fn(),
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
    examples: ["oil production by region", "downtime by month"],
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
  it("stays mounted while closed but out of reach", () => {
    // It slides away rather than unmounting, so the panel keeps its scroll
    // position and its in-flight requests. Being off-screen is not enough:
    // a hidden transcript full of focusable buttons is a tab trap nobody
    // can see, so it leaves the accessibility tree as well.
    setup({ open: false });
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    expect(document.querySelector(".chat-drawer")).toHaveAttribute(
      "data-open", "false");
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
    expect(screen.getByRole("switch", { name: /think harder/i }))
      .toBeDisabled();
  });

  it("offers escalation when the provider has two tiers", () => {
    setup({ provider: "anthropic" });
    expect(screen.getByRole("switch", { name: /think harder/i }))
      .toBeEnabled();
  });

  it("disables consent and says why when the server withholds it", () => {
    setup({ dataSharingPermitted: false });
    const box = screen.getByRole("switch",
                                 { name: /Reading the numbers is off/i });
    expect(box).toBeDisabled();
    expect(box).not.toBeChecked();
  });

  it("names the provider in the consent wording", () => {
    setup({ provider: "nvidia" });
    expect(screen.getByRole("switch",
                            { name: /NVIDIA \(free\) reads the numbers/i }))
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
    vi.mocked(chatApi.clearThread).mockResolvedValue({ id: "t2" } as never);
    const props = setup();

    await user.click(screen.getByRole("button", { name: "Clear" }));
    // The application's own dialog, not the browser's: it says what
    // happens on the button rather than offering OK.
    await user.click(await screen.findByRole("button", { name: "Clear it" }));

    await waitFor(() => expect(props.onThreadChange).toHaveBeenCalledWith("t2"));
  });

  it("keeps the conversation when clearing is declined", async () => {
    const user = userEvent.setup();
    const props = setup();

    await user.click(screen.getByRole("button", { name: "Clear" }));
    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(chatApi.clearThread).not.toHaveBeenCalled();
    expect(props.onThreadChange).not.toHaveBeenCalled();
  });

  it("asks before clearing rather than clearing and telling you", async () => {
    const user = userEvent.setup();
    setup();

    await user.click(screen.getByRole("button", { name: "Clear" }));

    expect(await screen.findByRole("dialog")).toBeVisible();
    expect(chatApi.clearThread).not.toHaveBeenCalled();
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
    // The button says what it does. "Apply" is the same word for adding a
    // card and for clearing a dashboard, which is how a confirmation stops
    // being read.
    expect(screen.getByRole("button", { name: "Rename it" })).toBeEnabled();
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

    await user.click(await screen.findByRole("button", { name: "Rename it" }));

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

  it("offers Undo against the change that message applied, not the last one",
     async () => {
    const user = userEvent.setup();
    vi.mocked(chatApi.getThread).mockResolvedValue({
      id: "t1",
      messages: [{
        id: "m1", role: "assistant", action: "applied",
        say: "Renamed to “Wells”.", action_id: "a9", created_at: "x",
      }],
    } as never);
    vi.mocked(chatApi.undoAction).mockResolvedValue({} as never);
    const props = setup();

    await user.click(await screen.findByRole("button", { name: "Undo this" }));

    expect(chatApi.undoAction).toHaveBeenCalledWith("a9");
    await waitFor(() => expect(props.onApplied).toHaveBeenCalled());
  });

  it("shows no Undo on a message that changed nothing", async () => {
    vi.mocked(chatApi.getThread).mockResolvedValue({
      id: "t1",
      messages: [{
        id: "m1", role: "assistant", action: "answer",
        say: "West Kazakhstan leads.", created_at: "x",
      }],
    } as never);
    setup();

    await screen.findByText("West Kazakhstan leads.");
    expect(screen.queryByRole("button", { name: "Undo this" }))
      .not.toBeInTheDocument();
  });

  it("says why an undo was refused rather than pretending it worked",
     async () => {
    const user = userEvent.setup();
    vi.mocked(chatApi.getThread).mockResolvedValue({
      id: "t1",
      messages: [{
        id: "m1", role: "assistant", action: "applied", say: "Renamed.",
        action_id: "a9", created_at: "x",
      }],
    } as never);
    vi.mocked(chatApi.undoAction).mockRejectedValue(
      new Error("That dashboard's name changed again after that"));
    const props = setup();

    await user.click(await screen.findByRole("button", { name: "Undo this" }));

    expect(await screen.findByText(/changed again after that/)).toBeVisible();
    expect(props.onApplied).not.toHaveBeenCalled();
  });

  // -- opening and closing ----------------------------------------------

  it("puts the caret in the composer when it opens", async () => {
    setup();
    await waitFor(() =>
      expect(screen.getByLabelText("Ask about this dashboard…"))
        .toHaveFocus());
  });

  it("closes on Escape from anywhere inside", async () => {
    const user = userEvent.setup();
    const props = setup();

    await user.keyboard("{Escape}");

    expect(props.onClose).toHaveBeenCalled();
  });

  it("marks a plan that removes things and says so on the button", async () => {
    vi.mocked(chatApi.getThread).mockResolvedValue({
      id: "t1", messages: [],
      pending_plan: {
        ...PLAN,
        action: "delete_card",
        say: "I will clear this dashboard.",
        operations: [
          { kind: "delete_card", summary: "Remove “One” from Operations" },
          { kind: "delete_card", summary: "Remove “Two” from Operations" },
        ],
      },
    } as never);
    setup();

    const apply = await screen.findByRole("button", { name: "Remove 2 cards" });
    expect(apply).toHaveClass("danger");
    expect(document.querySelector(".chat-plan")).toHaveClass("removes");
  });
});
