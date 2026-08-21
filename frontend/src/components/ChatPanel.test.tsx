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
  },
}));

const CAPABILITIES = {
  anthropic: { default_model: "haiku", strong_model: "sonnet", strong_available: true },
  nvidia: { default_model: "deepseek", strong_model: "deepseek", strong_available: false },
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
    ...over,
  };
  render(<ChatPanel {...props} />);
  return props;
}

beforeEach(() => {
  // Without this, a call recorded by an earlier test makes a later
  // "was not called" assertion pass or fail for the wrong reason.
  vi.clearAllMocks();
  vi.mocked(chatApi.getThread).mockResolvedValue({ id: "t1", messages: [] } as never);
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
    // NVIDIA runs one DeepSeek model for both tiers, so "think harder"
    // would promise something that cannot happen.
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
    expect(screen.getByLabelText(/DeepSeek read the numbers/i)).toBeInTheDocument();
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
    await user.click(screen.getByRole("radio", { name: "DeepSeek" }));
    expect(props.onProviderChange).toHaveBeenCalledWith("nvidia");
  });

  it("cannot send while no dashboard is open", () => {
    setup({ activeBoardId: null });
    expect(screen.getByLabelText("Ask about this dashboard…")).toBeDisabled();
  });
});
