import type { Render, SemanticQuery } from "./types.gen";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(await res.text().catch(() => res.statusText));
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export interface Layout { x: number; y: number; w: number; h: number }

export interface Card {
  id: string;
  board_id: string;
  title: string;
  semantic_query: SemanticQuery | null;
  chart_hint: string | null;
  state: "empty" | "ready" | "broken";
  can_undo: boolean;
  layout: Layout;
  ttl_seconds: number;
  render?: Render & { state: "empty" | "ready" | "broken" };
}

export interface BoardSummary {
  id: string;
  title: string;
  position: number;
}

export interface Board extends BoardSummary { cards: Card[] }

/** Three possible answers, never a confidently wrong chart: a rendered
 *  card, one clarifying question, or a refusal naming what is undefined. */
export type AskResult = {
  model?: string;
  provider?: Provider;
  /** What a refinement moved, stated deterministically. Empty for a fresh
   *  question, and empty for an edit that changed only the chart. */
  changed?: string[];
} & (
  | { state: "refused" | "clarify"; message: string }
  | ({ state: "ready" | "broken" } & Render)
);

export type Provider = "anthropic" | "gemini" | "openai" | "nvidia";

/** Only providers with a key configured; the selector offers no more than
 *  what can actually answer. */
export interface Providers { default: Provider; available: Provider[] }

export interface LayerField { name: string; label: string; type?: string; agg?: string }
export interface LayerInfo {
  entities: {
    name: string; label: string; description: string; unverified: string[];
    dimensions: LayerField[]; measures: LayerField[];
  }[];
  examples: string[];
  providers: Providers;
}

export const api = {
  listBoards: () => request<BoardSummary[]>("/boards"),
  createBoard: (title: string) =>
    request<BoardSummary>("/boards", { method: "POST", body: JSON.stringify({ title }) }),
  getBoard: (id: string) => request<Board>(`/boards/${id}`),
  updateBoard: (id: string, fields: { title?: string; position?: number }) =>
    request<BoardSummary>(`/boards/${id}`, {
      method: "PATCH",
      body: JSON.stringify(fields),
    }),
  reorderBoards: (order: string[]) =>
    request<void>("/boards/reorder", { method: "POST", body: JSON.stringify({ order }) }),
  deleteBoard: (id: string) => request<void>(`/boards/${id}`, { method: "DELETE" }),
  addCard: (boardId: string) => request<Card>(`/boards/${boardId}/cards`, { method: "POST" }),
  saveLayout: (boardId: string, layouts: Record<string, Layout>) =>
    request<void>(`/boards/${boardId}/layout`, {
      method: "PATCH",
      body: JSON.stringify({ layouts }),
    }),
  getCard: (id: string) => request<Card>(`/cards/${id}`),
  refreshCard: (id: string) => request<Card>(`/cards/${id}/refresh`, { method: "POST" }),
  undoCard: (id: string) => request<Card>(`/cards/${id}/undo`, { method: "POST" }),
  deleteCard: (id: string) => request<void>(`/cards/${id}`, { method: "DELETE" }),
  patchCard: (id: string, fields: { title?: string; ttl_seconds?: number }) =>
    request<Card>(`/cards/${id}`, { method: "PATCH", body: JSON.stringify(fields) }),
  layer: () => request<LayerInfo>("/layer"),
  ask: (question: string, cardId: string, hard = false, provider?: Provider) =>
    request<AskResult>("/ask", {
      method: "POST",
      body: JSON.stringify({ question, card_id: cardId, hard, provider }),
    }),
  runQuery: (body: {
    semantic_query: SemanticQuery;
    chart_hint?: string | null;
    title?: string;
    card_id?: string;
  }) => request<Render>("/query", { method: "POST", body: JSON.stringify(body) }),
};
