import { request } from "./client";
import type { Provider } from "./client";
import type {
  ChatThreadView,
  ChatTurnResponse,
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
};
