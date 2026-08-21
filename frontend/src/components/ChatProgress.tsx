import type { ActionProgressView } from "../api/types.gen";

/**
 * A dashboard filling in.
 *
 * The cards are already on the board by the time this appears — what is
 * still running is the question behind each one. So this counts questions
 * answered, not cards created, because the cards are the thing the person
 * can already see.
 */
export default function ChatProgress({
  action,
  onStop,
}: {
  action: ActionProgressView;
  onStop: () => void;
}) {
  const total = action.total ?? 0;
  const done = (action.completed ?? 0) + (action.failed ?? 0);
  const running = action.status === "pending" || action.status === "running";

  return (
    <li className="chat-turn chat-progress" aria-live="polite">
      <p className="eyebrow">
        {running ? "Building" : action.status === "stopped" ? "Stopped" : "Built"}
      </p>
      <p>
        {done} of {total} card{total === 1 ? "" : "s"}
        {(action.failed ?? 0) > 0 && (
          <>
            {" · "}
            <b>{action.failed} could not be built</b>
          </>
        )}
      </p>
      <div
        className="progress-rail"
        role="progressbar"
        aria-valuenow={done}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-label="Cards built"
      >
        <span style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
      </div>
      {running && (
        <div className="plan-actions">
          <button type="button" onClick={onStop}>
            Stop after this one
          </button>
        </div>
      )}
    </li>
  );
}
