import { describe, expect, it } from "vitest";
import { toCsv } from "./csv";
import { toDashboardJson } from "./json";
import { safeName } from "./png";
import type { Card } from "../api/client";

function card(over: Partial<Card> = {}): Card {
  return {
    id: "c1",
    board_id: "b1",
    title: "Oil by region",
    semantic_query: { entity: "production", measures: ["oil"] } as never,
    chart_hint: null,
    state: "ready",
    can_undo: false,
    layout: { x: 0, y: 0, w: 6, h: 10 },
    ttl_seconds: 900,
    ...over,
  };
}

describe("toCsv", () => {
  it("writes a header from the row keys", () => {
    expect(toCsv([{ region: "Atyrau", oil: 12 }])).toBe(
      "region,oil\r\nAtyrau,12\r\n",
    );
  });

  it("is empty for no rows rather than a lone header", () => {
    expect(toCsv([])).toBe("");
  });

  it("quotes values containing a comma, quote or newline", () => {
    const csv = toCsv([{ a: "x,y", b: 'say "hi"', c: "line\nbreak" }]);
    expect(csv).toContain('"x,y"');
    expect(csv).toContain('"say ""hi"""');
    expect(csv).toContain('"line\nbreak"');
  });

  it("writes an empty field for null and undefined", () => {
    expect(toCsv([{ a: null, b: undefined, c: 0 }])).toBe("a,b,c\r\n,,0\r\n");
  });

  it("keeps a column that only later rows carry", () => {
    // A first row missing a nullable column must not drop it for everyone.
    const csv = toCsv([{ a: 1 }, { a: 2, b: 3 }]);
    expect(csv).toBe("a,b\r\n1,\r\n2,3\r\n");
  });

  it("does not treat zero or false as missing", () => {
    expect(toCsv([{ n: 0, flag: false }])).toBe("n,flag\r\n0,false\r\n");
  });
});

describe("toDashboardJson", () => {
  it("carries the semantic query rather than the rendered rows", () => {
    // The stored artifact is the query everywhere else in this app; an
    // export of frozen numbers would rot the way frozen SQL does.
    const out = toDashboardJson("Operations", [card()]);
    expect(out.cards[0].semantic_query).toEqual({
      entity: "production",
      measures: ["oil"],
    });
    expect(JSON.stringify(out)).not.toContain("rows");
  });

  it("omits empty cards", () => {
    const out = toDashboardJson("Operations", [
      card(),
      card({ id: "c2", semantic_query: null, state: "empty" }),
    ]);
    expect(out.cards).toHaveLength(1);
  });

  it("keeps layout so the dashboard can be rebuilt as it looked", () => {
    const out = toDashboardJson("Operations", [card()]);
    expect(out.cards[0].layout).toEqual({ x: 0, y: 0, w: 6, h: 10 });
  });

  it("stamps a version so a later reader knows the shape", () => {
    expect(toDashboardJson("Operations", []).version).toBe(1);
  });
});

describe("safeName", () => {
  it("slugifies a title and dates it", () => {
    expect(safeName("Oil by Region!", "png")).toMatch(
      /^oil-by-region-\d{4}-\d{2}-\d{2}\.png$/,
    );
  });

  it("falls back when a title has nothing usable in it", () => {
    expect(safeName("!!!", "csv")).toMatch(/^untitled-/);
    expect(safeName("", "csv")).toMatch(/^untitled-/);
  });

  it("bounds the length so a long title cannot break the filesystem", () => {
    expect(safeName("x".repeat(400), "png").length).toBeLessThan(80);
  });
});
