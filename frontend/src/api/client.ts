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
  layout: Layout;
  ttl_seconds: number;
  render?: Render & { state: "empty" | "ready" | "broken" };
}

export interface Board { id: string; title: string; cards: Card[] }

/** Three possible answers, never a confidently wrong chart: a rendered
 *  card, one clarifying question, or a refusal naming what is undefined. */
export type AskResult = { model?: string } & (
  | { state: "refused" | "clarify"; message: string }
  | ({ state: "ready" | "broken" } & Render)
);

export interface LayerField { name: string; label: string; type?: string; agg?: string }
export interface LayerInfo {
  entities: {
    name: string; label: string; description: string; unverified: string[];
    dimensions: LayerField[]; measures: LayerField[];
  }[];
  examples: string[];
}

export const api = {
  listBoards: () => request<{ id: string; title: string }[]>("/boards"),
  createBoard: (title: string) =>
    request<{ id: string }>("/boards", { method: "POST", body: JSON.stringify({ title }) }),
  getBoard: (id: string) => request<Board>(`/boards/${id}`),
  addCard: (boardId: string) => request<Card>(`/boards/${boardId}/cards`, { method: "POST" }),
  saveLayout: (boardId: string, layouts: Record<string, Layout>) =>
    request<void>(`/boards/${boardId}/layout`, {
      method: "PATCH",
      body: JSON.stringify({ layouts }),
    }),
  getCard: (id: string) => request<Card>(`/cards/${id}`),
  refreshCard: (id: string) => request<Card>(`/cards/${id}/refresh`, { method: "POST" }),
  deleteCard: (id: string) => request<void>(`/cards/${id}`, { method: "DELETE" }),
  layer: () => request<LayerInfo>("/layer"),
  ask: (question: string, cardId: string, hard = false) =>
    request<AskResult>("/ask", {
      method: "POST",
      body: JSON.stringify({ question, card_id: cardId, hard }),
    }),
  runQuery: (body: {
    semantic_query: SemanticQuery;
    chart_hint?: string | null;
    title?: string;
    card_id?: string;
  }) => request<Render>("/query", { method: "POST", body: JSON.stringify(body) }),
};
