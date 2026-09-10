import type { ChatErrorEnvelope, Render, SemanticQuery } from "./types.gen";

const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type ApiErrorEnvelope = ChatErrorEnvelope;

export class ApiError extends Error {
  readonly code: string;
  readonly requestId: string;
  readonly retryable: boolean;

  constructor(envelope: ApiErrorEnvelope) {
    super(envelope.message);
    this.name = "ApiError";
    this.code = envelope.code;
    this.requestId = envelope.request_id;
    this.retryable = envelope.retryable;
  }
}

export interface LayoutConflictEnvelope extends LayoutResult {
  code: "layout_conflict";
  message: string;
}

export class LayoutConflictError extends Error {
  readonly layouts: Record<string, Layout>;
  readonly revision: number;

  constructor(envelope: LayoutConflictEnvelope) {
    super(envelope.message);
    this.name = "LayoutConflictError";
    this.layouts = envelope.layouts;
    this.revision = envelope.revision;
  }
}

const GENERIC_SERVER_ERROR =
  "We couldn’t finish that request. Nothing changed. Try again.";

function isApiErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (!value || typeof value !== "object") return false;
  const body = value as Record<string, unknown>;
  return typeof body.code === "string"
    && typeof body.message === "string"
    && typeof body.request_id === "string"
    && typeof body.retryable === "boolean";
}

function isLayoutConflictEnvelope(value: unknown): value is LayoutConflictEnvelope {
  if (!value || typeof value !== "object") return false;
  const body = value as Record<string, unknown>;
  return body.code === "layout_conflict"
    && typeof body.message === "string"
    && typeof body.revision === "number"
    && Boolean(body.layouts) && typeof body.layouts === "object";
}

/** Exported so the chat client shares one base URL and one error rule.
 *  A second fetch wrapper is how two halves of an app end up disagreeing
 *  about what a failure looks like. */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body: unknown = await res.json().catch(() => undefined);
    if (res.status === 409 && isLayoutConflictEnvelope(body)) {
      throw new LayoutConflictError(body);
    }
    if (isApiErrorEnvelope(body)) throw new ApiError(body);
    if (res.status >= 500) throw new Error(GENERIC_SERVER_ERROR);
    if (body && typeof body === "object"
        && typeof (body as { detail?: unknown }).detail === "string") {
      throw new Error((body as { detail: string }).detail);
    }
    throw new Error(res.statusText || "That request could not be completed.");
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export interface Layout { x: number; y: number; w: number; h: number }
export type LayoutMode = "free" | "auto_pack";

export interface LayoutResult {
  layouts: Record<string, Layout>;
  revision: number;
}

export interface PendingCardExchange {
  /** Rows written before refusals became replyable have no kind and are
   * clarifying questions. Keep that default at the client boundary too. */
  kind?: "clarify" | "refused";
  question: string;
  asked: string;
}

export interface Card {
  id: string;
  board_id: string;
  title: string;
  semantic_query: SemanticQuery | null;
  chart_hint: string | null;
  state: "empty" | "ready" | "broken";
  can_undo: boolean;
  auto_size_pending: boolean;
  layout: Layout;
  ttl_seconds: number;
  pending_clarification?: PendingCardExchange | null;
  render?: Render & { state: "empty" | "ready" | "broken" };
}

export interface BoardSummary {
  id: string;
  title: string;
  position: number;
  layout_mode: LayoutMode;
  /** The warehouse schema this dashboard asks its questions of. */
  schema_name: string;
  revision: number;
}

/** One table as the catalogue reports it. `entity` is what makes a row
 *  answerable; everything else on the row explains why it is not. */
export interface SchemaTable {
  table: string;
  is_view: boolean;
  entity: string | null;
  label: string;
  unverified: string[];
  joined_only: boolean;
}

export interface SchemaInfo {
  schema: string;
  tables: SchemaTable[];
  answerable: number;
}

export interface Board extends BoardSummary { cards: Card[] }

export interface DuplicateCardResult extends LayoutResult { card: Card }

/** Three possible answers, never a confidently wrong chart: a rendered
 *  card, one clarifying question, or a refusal naming what is undefined. */
export type AskResult = {
  model?: string;
  provider?: Provider;
  /** What a refinement moved, stated deterministically. Empty for a fresh
   *  question, and empty for an edit that changed only the chart. */
  changed?: string[];
} & (
  | {
      state: "refused" | "clarify";
      message: string;
      /** False for capacity/credential/transport failures. Those should be
       * retried as requests, not answered as if they were model questions. */
      replyable?: boolean;
    }
  | ({ state: "ready" | "broken" } & Render)
);

export type Provider = "anthropic" | "gemini" | "openai" | "nvidia";

/** Only providers with a key configured; the selector offers no more than
 *  what can actually answer. */
export interface ProviderCapability {
  default_model: string;
  strong_model: string;
  /** False when a provider's two tiers are the same model id, as NVIDIA's
   *  deliberately are. Offering "think harder" there would promise an
   *  escalation that cannot happen. */
  strong_available: boolean;
}

export interface Providers {
  default: Provider;
  available: Provider[];
  capabilities?: Record<string, ProviderCapability>;
}

/** Both server gates, stated separately so the consent control can say
 *  which one is closed. */
export interface ChatGates {
  enabled: boolean;
  data_sharing_permitted: boolean;
}

export interface LayerField { name: string; label: string; type?: string; agg?: string }
export interface LayerInfo {
  entities: {
    name: string; label: string; description: string; unverified: string[];
    dimensions: LayerField[]; measures: LayerField[];
  }[];
  examples: string[];
  /** Every schema the layer can answer from, whatever this call was
   *  scoped to. The picker needs the whole list, not the current slice. */
  schemas: string[];
  providers: Providers;
  chat?: ChatGates;
}

export const api = {
  listBoards: () => request<BoardSummary[]>("/boards"),
  createBoard: (title: string, schemaName?: string) =>
    request<BoardSummary>("/boards", {
      method: "POST",
      // Inherited from the board in front of you, so a second dashboard on
      // the same schema costs no extra step.
      body: JSON.stringify({ title, schema_name: schemaName }),
    }),
  getBoard: (id: string) => request<Board>(`/boards/${id}`),
  updateBoard: (id: string, fields: {
    title?: string;
    position?: number;
    layout_mode?: LayoutMode;
    schema_name?: string;
  }) =>
    request<BoardSummary>(`/boards/${id}`, {
      method: "PATCH",
      body: JSON.stringify(fields),
    }),
  reorderBoards: (order: string[]) =>
    request<void>("/boards/reorder", { method: "POST", body: JSON.stringify({ order }) }),
  duplicateBoard: (id: string, title?: string) =>
    request<BoardSummary>(`/boards/${id}/duplicate`, {
      method: "POST",
      body: JSON.stringify(title ? { title } : {}),
    }),
  deleteBoard: (id: string) => request<void>(`/boards/${id}`, { method: "DELETE" }),
  addCard: (boardId: string) => request<Card>(`/boards/${boardId}/cards`, { method: "POST" }),
  saveLayout: (
    boardId: string,
    layouts: Record<string, Layout>,
    manuallyResized: string[],
    expectedRevision: number,
  ) =>
    request<LayoutResult>(`/boards/${boardId}/layout`, {
      method: "PATCH",
      body: JSON.stringify({
        layouts,
        manually_resized: manuallyResized,
        expected_revision: expectedRevision,
      }),
    }),
  getCard: (id: string) => request<Card>(`/cards/${id}`),
  duplicateCard: (id: string) =>
    request<DuplicateCardResult>(`/cards/${id}/duplicate`, { method: "POST" }),
  refreshCard: (id: string) => request<Card>(`/cards/${id}/refresh`, { method: "POST" }),
  undoCard: (id: string) => request<Card>(`/cards/${id}/undo`, { method: "POST" }),
  deleteCard: (id: string) => request<void>(`/cards/${id}`, { method: "DELETE" }),
  dismissCardExchange: (id: string) =>
    request<void>(`/cards/${id}/dismiss-exchange`, { method: "POST" }),
  patchCard: (id: string, fields: { title?: string; ttl_seconds?: number }) =>
    request<Card>(`/cards/${id}`, { method: "PATCH", body: JSON.stringify(fields) }),
  layer: (schemaName?: string) =>
    request<LayerInfo>(
      schemaName ? `/layer?schema=${encodeURIComponent(schemaName)}` : "/layer"),
  schemas: () => request<SchemaInfo[]>("/schemas"),
  /** `reply` says whether this answers what the card last said. Always
   *  stated, never left out: the card may still be holding an exchange the
   *  asker has walked away from, and only the caller knows which it is. */
  ask: (question: string, cardId: string, hard = false, provider?: Provider,
        reply = false) =>
    request<AskResult>("/ask", {
      method: "POST",
      body: JSON.stringify({ question, card_id: cardId, hard, provider, reply }),
    }),
  runQuery: (body: {
    semantic_query: SemanticQuery;
    chart_hint?: string | null;
    title?: string;
    card_id?: string;
  }) => request<Render>("/query", { method: "POST", body: JSON.stringify(body) }),
};
