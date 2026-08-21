import type { ChatMessageOut } from "../api/types.gen";

/** One turn in the transcript.
 *
 *  The assistant's prose has already been through claim verification on the
 *  server: anything it says that could not be traced back to a row has been
 *  removed, not flagged. So there is no "possibly wrong" styling here, by
 *  design — a warning label next to a number still reads as probably true.
 */
export default function ChatMessage({
  message,
  boards,
  undoing,
  onNavigate,
  onUndo,
}: {
  message: ChatMessageOut;
  boards: { id: string; title: string }[];
  undoing: boolean;
  onNavigate: (boardId: string, cardId?: string) => void;
  onUndo: (actionId: string) => void;
}) {
  if (message.role === "user") {
    return (
      <li className="chat-turn chat-user">
        <p>{message.say}</p>
        {/* Which dashboard this was asked on. The conversation spans all of
            them, so "this board" three turns ago needs a label. */}
        {message.active_board_title && (
          <span className="eyebrow">on {message.active_board_title}</span>
        )}
      </li>
    );
  }

  const body = message.refusal ?? message.clarify ?? message.say;
  const kind =
    message.action === "refuse"
      ? "chat-refuse"
      : message.action === "clarify"
        ? "chat-clarify"
        : "chat-answer";

  return (
    <li className={`chat-turn chat-assistant ${kind}`}>
      {body && <p>{body}</p>}

      {(message.claims ?? []).length > 0 && (
        <ul className="chat-sources">
          {(message.claims ?? []).flatMap((claim, i) =>
            (claim.sources ?? []).map((source, j) => {
              // A card can be deleted while the transcript still refers to
              // it. The chip stays, dead, rather than the sentence quietly
              // losing its evidence.
              const alive = boards.some((b) => b.id === source.board_id);
              return (
                <li key={`${i}-${j}`}>
                  <button
                    type="button"
                    className={alive ? "chip" : "chip dead"}
                    disabled={!alive}
                    title={alive
                      ? `Go to ${source.card_title}`
                      : "That card is no longer on any dashboard"}
                    onClick={() => onNavigate(source.board_id, source.card_id)}
                  >
                    {source.card_title || "Untitled card"}
                  </button>
                </li>
              );
            }),
          )}
        </ul>
      )}

      {message.missing_metric && (
        <div className="notice hint chat-request">
          <p>
            <b>{message.missing_metric}</b> is not in the semantic layer.
          </p>
          {message.request_text && (
            <>
              <p className="eyebrow">Send this to whoever maintains it</p>
              <textarea readOnly rows={2} value={message.request_text} />
            </>
          )}
        </div>
      )}

      {/* A change reverses as one thing, however many cards it made. The
          button names this change rather than "the last one", which stops
          meaning anything the moment a second tab is open. */}
      {message.action === "applied" && message.action_id && (
        <div className="plan-actions">
          <button
            type="button"
            disabled={undoing}
            onClick={() => onUndo(message.action_id!)}
          >
            Undo this
          </button>
        </div>
      )}

      {message.data_exposed && (
        <span className="eyebrow chat-exposed" title="Row values from the
          dashboard were in scope for this answer">
          read the visible rows
        </span>
      )}
    </li>
  );
}
