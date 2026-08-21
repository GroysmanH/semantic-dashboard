import { request } from "./client";
import type { Provider } from "./client";
import type {
  ActionProgressView,
  ChatEventEnvelope,
  ChatMessageOut,
  ChatThreadView,
  ChatTurnResponse,
  PlanConfirmedView,
  TransientResultView,
} from "./types.gen";

export interface ChatTurnIn {
  active_board_id: string;
  question: string;
  provider?: Provider;
  hard?: boolean;
  share_visible_data?: boolean;
  selected_card_id?: string | null;
}

/** Chat is absent rather than forbidden when the server has it switched
 *  off, so a 404 here means "not enabled", not "broken". */
export const chatApi = {
  createThread: () =>
    request<{ id: string }>("/chat/threads", { method: "POST" }),
  getThread: (threadId: string) =>
    request<ChatThreadView>(`/chat/threads/${threadId}`),
  clearThread: (threadId: string) =>
    request<{ id: string }>(`/chat/threads/${threadId}`, { method: "DELETE" }),
  sendTurn: (threadId: string, body: ChatTurnIn) =>
    request<ChatTurnResponse>(`/chat/threads/${threadId}/turns`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rerunTransient: (resultId: string) =>
    request<TransientResultView>(`/chat/transient/${resultId}/rerun`, {
      method: "POST",
    }),

  /** Authorises exactly the plan that was shown. The provider is sent
   *  again because a plan can sit unconfirmed while the setting changes,
   *  and the one on screen is the one that should pay. */
  confirmPlan: (planId: string, body: { provider?: Provider; hard?: boolean }) =>
    request<PlanConfirmedView>(`/chat/plans/${planId}/confirm`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelPlan: (planId: string) =>
    request<ChatMessageOut>(`/chat/plans/${planId}/cancel`, {
      method: "POST",
    }),

  actionProgress: (actionId: string) =>
    request<ActionProgressView>(`/chat/actions/${actionId}`),
  actionEvents: (actionId: string, after = 0) =>
    request<ChatEventEnvelope[]>(
      `/chat/actions/${actionId}/events?after=${after}`),
  /** Reverses one confirmed change as a single thing, whatever shape it
   *  was. Refused rather than forced when something happened after it. */
  undoAction: (actionId: string) =>
    request<ChatMessageOut>(`/chat/actions/${actionId}/undo`, {
      method: "POST",
    }),
  stopAction: (actionId: string) =>
    request<ActionProgressView>(`/chat/actions/${actionId}/stop`, {
      method: "POST",
    }),
};
