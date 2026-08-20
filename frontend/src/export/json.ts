import type { Card } from "../api/client";

/** A dashboard definition, not a snapshot of its numbers. The semantic
 *  query is the stored artifact everywhere else in this app; an export that
 *  froze rows instead would rot exactly the way frozen SQL does. */
export interface DashboardExport {
  version: 1;
  title: string;
  exported_at: string;
  cards: {
    title: string;
    semantic_query: unknown;
    chart_hint: string | null;
    layout: { x: number; y: number; w: number; h: number };
    ttl_seconds: number;
  }[];
}

export function toDashboardJson(title: string, cards: Card[]): DashboardExport {
  return {
    version: 1,
    title,
    exported_at: new Date().toISOString(),
    // Empty cards are placeholders for a layout, not content worth carrying
    // to another instance.
    cards: cards
      .filter((c) => c.semantic_query)
      .map((c) => ({
        title: c.title,
        semantic_query: c.semantic_query,
        chart_hint: c.chart_hint,
        layout: c.layout,
        ttl_seconds: c.ttl_seconds,
      })),
  };
}

export function dashboardBlob(title: string, cards: Card[]): Blob {
  return new Blob([JSON.stringify(toDashboardJson(title, cards), null, 2)], {
    type: "application/json",
  });
}
