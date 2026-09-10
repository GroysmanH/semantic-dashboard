import { beforeEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_WIDTH, MAX_WIDTH, MIN_WIDTH, prefs } from "./preferences";

describe("preferences", () => {
  beforeEach(() => localStorage.clear());

  it("starts closed and unconsented on a new browser", () => {
    // A chat that opens itself has decided this is a chat application.
    expect(prefs.open()).toBe(false);
    expect(prefs.shareData()).toBe(false);
    expect(prefs.pinned()).toBe(false);
    expect(prefs.threadId()).toBeNull();
  });

  it("remembers the drawer state across a reload", () => {
    prefs.setOpen(true);
    prefs.setPinned(true);
    prefs.setShareData(true);
    expect([prefs.open(), prefs.pinned(), prefs.shareData()]).toEqual([true, true, true]);
  });

  it("clamps a width to the usable range", () => {
    prefs.setWidth(50);
    expect(prefs.width()).toBe(MIN_WIDTH);
    prefs.setWidth(5000);
    expect(prefs.width()).toBe(MAX_WIDTH);
  });

  it("falls back when a stored width is nonsense", () => {
    // Anything in storage was written by an older build or by hand.
    localStorage.setItem("semantic-dashboard:chat-width", "not a number");
    expect(prefs.width()).toBe(DEFAULT_WIDTH);
  });

  it("rejects a provider that is not one of ours", () => {
    localStorage.setItem("semantic-dashboard:provider", "some-other-vendor");
    expect(prefs.provider("gemini")).toBe("gemini");
  });

  it("keeps a provider that is", () => {
    prefs.setProvider("openai");
    expect(prefs.provider("anthropic")).toBe("openai");
  });

  it("survives storage being unavailable", () => {
    // Private windows throw on access rather than returning null, and a
    // dashboard that will not load because it could not read a width is a
    // worse failure than forgetting the width.
    const get = vi.spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => { throw new Error("blocked"); });
    const set = vi.spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => { throw new Error("blocked"); });

    expect(() => prefs.setOpen(true)).not.toThrow();
    expect(prefs.open()).toBe(false);
    expect(prefs.width()).toBe(DEFAULT_WIDTH);
    expect(prefs.provider("gemini")).toBe("gemini");

    get.mockRestore();
    set.mockRestore();
  });

  it("clears a thread id rather than storing null", () => {
    prefs.setThreadId("abc");
    prefs.setThreadId(null);
    expect(prefs.threadId()).toBeNull();
  });
});
