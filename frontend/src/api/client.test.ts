import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, LayoutConflictError, api, request } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("request errors", () => {
  it("parses the safe API error envelope", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: "chat_unexpected_error",
      message: "Nothing changed. Try again.",
      request_id: "req-1",
      retryable: true,
    }), { status: 500, headers: { "content-type": "application/json" } })));

    await expect(request("/chat/test")).rejects.toMatchObject({
      name: "ApiError",
      code: "chat_unexpected_error",
      message: "Nothing changed. Try again.",
      requestId: "req-1",
      retryable: true,
    } satisfies Partial<ApiError>);
  });

  it.each([
    ["non-JSON", "raw upstream stack and provider payload", "text/plain"],
    ["unknown JSON", JSON.stringify({ detail: "raw provider payload" }),
      "application/json"],
  ])("never exposes an %s server failure", async (_kind, body, contentType) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, {
      status: 502, headers: { "content-type": contentType },
    })));

    const error = await request("/chat/test").catch((caught: Error) => caught);
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe(
      "We couldn’t finish that request. Nothing changed. Try again.",
    );
    expect((error as Error).message).not.toContain("provider");
  });

  it("parses a typed layout conflict without losing canonical recovery state", async () => {
    const layouts = { card: { x: 0, y: 0, w: 6, h: 10 } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: "layout_conflict",
      message: "This dashboard changed while you were arranging it.",
      layouts,
      revision: 8,
    }), { status: 409, headers: { "content-type": "application/json" } })));

    const error = await request("/boards/board/layout").catch((caught) => caught);

    expect(error).toBeInstanceOf(LayoutConflictError);
    expect(error).toMatchObject({ layouts, revision: 8 });
  });

  it("sends the board revision as the layout write precondition", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      layouts: { card: { x: 0, y: 0, w: 6, h: 10 } },
      revision: 9,
    }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);

    await api.saveLayout(
      "board",
      { card: { x: 0, y: 0, w: 6, h: 10 } },
      [],
      8,
    );

    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({
      layouts: { card: { x: 0, y: 0, w: 6, h: 10 } },
      manually_resized: [],
      expected_revision: 8,
    });
  });
});
