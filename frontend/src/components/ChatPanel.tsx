import { useCallback, useEffect, useRef, useState } from "react";
import type { BoardSummary, Provider } from "../api/client";
import type {
  ActionProgressView,
  ChatMessageOut,
  PendingPlanView,
  TransientResultView,
} from "../api/types.gen";
import { chatApi } from "../api/chat";
import { MAX_WIDTH, MIN_WIDTH } from "../state/preferences";
import AskBar from "./AskBar";
import ChatMessage from "./ChatMessage";
import ChatPlan from "./ChatPlan";
import ChatProgress from "./ChatProgress";
import ChatResult from "./ChatResult";
import ProviderPicker, { PROVIDER_LABEL } from "./ProviderPicker";

// How often a running generation is asked where it got to. Cards are built
// one model call at a time, so anything faster is asking a question whose
// answer cannot have changed.
const POLL_MS = 1200;

export interface ProviderCapability {
  default_model: string;
  strong_model: string;
  strong_available: boolean;
}

/**
 * The conversation, spanning every dashboard.
 *
 * Mounted once at the app level and never keyed on the board, so switching
 * tabs does not restart the thread. Which dashboard each turn was asked on
 * is recorded per message instead.
 */
export default function ChatPanel({
  threadId,
  open,
  pinned,
  width,
  activeBoardId,
  activeBoardTitle,
  boards,
  provider,
  providers,
  capabilities,
  shareVisibleData,
  dataSharingPermitted,
  selectedCardId,
  onClose,
  onPinnedChange,
  onWidthChange,
  onProviderChange,
  onConsentChange,
  onThreadChange,
  onNavigate,
  onApplied,
}: {
  threadId: string | null;
  open: boolean;
  pinned: boolean;
  width: number;
  activeBoardId: string | null;
  activeBoardTitle: string;
  boards: BoardSummary[];
  provider: Provider;
  providers: Provider[];
  capabilities: Record<string, ProviderCapability>;
  shareVisibleData: boolean;
  dataSharingPermitted: boolean;
  selectedCardId: string | null;
  onClose: () => void;
  onPinnedChange: (v: boolean) => void;
  onWidthChange: (px: number) => void;
  onProviderChange: (p: Provider) => void;
  onConsentChange: (v: boolean) => void;
  onThreadChange: (id: string) => void;
  onNavigate: (boardId: string, cardId?: string) => void;
  /** A change landed. The tab bar and the grid are both stale now, and
   *  only the app above knows how to reload them. */
  onApplied: (boardId: string | null) => void | Promise<void>;
}) {
  const [messages, setMessages] = useState<ChatMessageOut[]>([]);
  const [results, setResults] = useState<Record<string, TransientResultView>>({});
  const [openSql, setOpenSql] = useState<string | null>(null);
  const [hard, setHard] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [plan, setPlan] = useState<PendingPlanView | null>(null);
  const [action, setAction] = useState<ActionProgressView | null>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const strong = capabilities[provider]?.strong_available ?? false;

  useEffect(() => {
    if (!threadId || !open) return;
    (async () => {
      try {
        const thread = await chatApi.getThread(threadId);
        setMessages(thread.messages ?? []);
        // A plan and a running build both outlive this tab. Someone who
        // reloads mid-decision finds the same thing waiting.
        setPlan(thread.pending_plan ?? null);
        setAction((thread.active_actions ?? [])[0] ?? null);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [threadId, open]);

  useEffect(() => {
    const log = listRef.current;
    // Optional-called: jsdom has no scrollTo, and neither do some older
    // engines. Failing to scroll is not worth throwing over.
    log?.scrollTo?.({ top: log.scrollHeight });
  }, [messages.length]);

  const send = async (question: string) => {
    if (!threadId || !activeBoardId) return;
    setBusy(true);
    setError(null);
    try {
      const response = await chatApi.sendTurn(threadId, {
        active_board_id: activeBoardId,
        question,
        provider,
        hard,
        share_visible_data: shareVisibleData,
        selected_card_id: selectedCardId,
      });
      const thread = await chatApi.getThread(threadId);
      setMessages(thread.messages ?? []);
      setPlan(response.pending_plan ?? null);
      if (response.transient_result) {
        setResults((prev) => ({
          ...prev,
          [response.message.id]: response.transient_result!,
        }));
      }
      // Only this form's checkbox resets: escalation is a decision about
      // one question, not a mode.
      setHard(false);
    } catch (e) {
      // Shown in the transcript, not a toast that vanishes before it is
      // read. A failed turn is part of the conversation.
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    if (!threadId) return;
    if (!window.confirm("Clear this conversation? It cannot be recovered.")) return;
    try {
      const fresh = await chatApi.clearThread(threadId);
      onThreadChange(fresh.id);
      setMessages([]);
      setResults({});
      setPlan(null);
      setAction(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const rerun = async (messageId: string, resultId: string) => {
    setBusy(true);
    try {
      const fresh = await chatApi.rerunTransient(resultId);
      setResults((prev) => ({ ...prev, [messageId]: fresh }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const refresh = useCallback(async () => {
    if (!threadId) return;
    const thread = await chatApi.getThread(threadId);
    setMessages(thread.messages ?? []);
    setPlan(thread.pending_plan ?? null);
  }, [threadId]);

  const confirm = async () => {
    if (!plan || !threadId) return;
    setBusy(true);
    setError(null);
    try {
      const applied = await chatApi.confirmPlan(plan.id, { provider, hard });
      setPlan(null);
      setAction(applied.action ?? null);
      await refresh();
      // Before the cards finish building, not after: the empty ones are
      // already on the board and hiding them until the end would make a
      // dashboard appear all at once, which is the thing being avoided.
      await onApplied(applied.board_id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // The plan may have gone stale rather than failed, and the reason is
      // on the server. Re-read it instead of guessing.
      await refresh().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    if (!plan) return;
    setBusy(true);
    try {
      await chatApi.cancelPlan(plan.id);
      setPlan(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const undo = async (actionId: string) => {
    setBusy(true);
    setError(null);
    try {
      await chatApi.undoAction(actionId);
      // The build that made those cards has nothing left to report.
      setAction(null);
      await refresh();
      await onApplied(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!action) return;
    try {
      setAction(await chatApi.stopAction(action.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Follow a running build. A poll rather than a stream: the same events,
  // and a reconnect is just the next request.
  useEffect(() => {
    if (!action) return;
    if (action.status !== "pending" && action.status !== "running") return;

    let live = true;
    const timer = window.setInterval(async () => {
      try {
        const next = await chatApi.actionProgress(action.id);
        if (!live) return;
        setAction(next);
        // Reload the board on every step, so each card appears as it is
        // finished rather than all of them at the end.
        await onApplied(next.board_id ?? null);
      } catch {
        // A progress read that fails is not worth interrupting anyone
        // over; the next tick tries again.
      }
    }, POLL_MS);

    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [action, onApplied]);

  // Dragging the edge. Width is a preference, never part of a board's
  // saved layout: pinning must not move anybody's cards.
  const startResize = useCallback((event: React.PointerEvent) => {
    event.preventDefault();
    const move = (e: PointerEvent) => {
      const next = window.innerWidth - e.clientX;
      onWidthChange(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, next)));
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }, [onWidthChange]);

  if (!open) return null;

  return (
    <aside
      className={pinned ? "chat-drawer pinned" : "chat-drawer"}
      style={{ width }}
      aria-label="Assistant"
    >
      <div
        className="chat-resize"
        onPointerDown={startResize}
        role="separator"
        aria-label="Resize the assistant"
        aria-orientation="vertical"
      />

      <header className="chat-head">
        <span className="eyebrow">Assistant</span>
        <span className="spacer" />
        <button type="button" onClick={() => onPinnedChange(!pinned)}
                aria-pressed={pinned}>
          {pinned ? "Unpin" : "Pin"}
        </button>
        <button type="button" onClick={() => void clear()}>Clear</button>
        <button type="button" onClick={onClose} aria-label="Close the assistant">
          ×
        </button>
      </header>

      <p className="eyebrow chat-scope">
        Looking at <b>{activeBoardTitle || "no dashboard"}</b>
      </p>

      <ul className="chat-log" ref={listRef}>
        {messages.length === 0 && (
          <li className="chat-turn chat-empty">
            <p>Ask about the charts on screen, or ask for a new one.</p>
          </li>
        )}
        {messages.map((message) => (
          <div key={message.id}>
            <ChatMessage
              message={message}
              boards={boards}
              undoing={busy}
              onNavigate={onNavigate}
              onUndo={(id) => void undo(id)}
            />
            {results[message.id] && (
              <ChatResult
                result={results[message.id]}
                sqlOpen={openSql === message.id}
                onSqlToggle={() =>
                  setOpenSql(openSql === message.id ? null : message.id)}
                onRerun={() => void rerun(message.id, results[message.id].id)}
                busy={busy}
              />
            )}
          </div>
        ))}
        {plan && (
          <ChatPlan
            plan={plan}
            busy={busy}
            onConfirm={() => void confirm()}
            onCancel={() => void discard()}
          />
        )}
        {action && (
          <ChatProgress action={action} onStop={() => void stop()} />
        )}
        {error && <li className="chat-turn"><p className="notice broken">{error}</p></li>}
      </ul>

      <div className="chat-compose">
        <AskBar
          placeholder="Ask about this dashboard…"
          submitLabel="Send"
          busy={busy || !activeBoardId}
          onSubmit={send}
        />

        <ProviderPicker
          provider={provider}
          providers={providers}
          busy={busy}
          onChange={onProviderChange}
        />

        <label
          className="harder"
          title={strong
            ? "Costs more. Use it when a question needs care."
            : `${PROVIDER_LABEL[provider] ?? provider} has one model tier, so there is nothing to escalate to.`}
        >
          <input
            type="checkbox"
            checked={hard && strong}
            onChange={(e) => setHard(e.target.checked)}
            disabled={busy || !strong}
          />
          <span>This one is hard — think harder about it</span>
        </label>

        <label
          className="harder"
          title={dataSharingPermitted
            ? `Sends the rows already on screen to ${PROVIDER_LABEL[provider] ?? provider}.`
            : "Turned off on the server. Nothing on this dashboard can be sent to a model."}
        >
          <input
            type="checkbox"
            checked={shareVisibleData && dataSharingPermitted}
            onChange={(e) => onConsentChange(e.target.checked)}
            disabled={busy || !dataSharingPermitted}
          />
          <span>
            {dataSharingPermitted
              ? `Let ${PROVIDER_LABEL[provider] ?? provider} read the numbers on screen`
              : "Reading the numbers on screen is disabled on the server"}
          </span>
        </label>
      </div>
    </aside>
  );
}
