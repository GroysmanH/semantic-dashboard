import type { PendingPlanView } from "../api/types.gen";

/**
 * The change, before it happens.
 *
 * Every line is something the reader can check against the board in front
 * of them, which is the whole point: a confirmation button is only worth
 * pressing if what it authorises is legible. Nothing here is written by a
 * model — the sentences come from the server's resolved plan, so the
 * preview and the effect are two readings of one document.
 */
export default function ChatPlan({
  plan,
  busy,
  onConfirm,
  onCancel,
}: {
  plan: PendingPlanView;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const operations = plan.operations ?? [];

  return (
    <li className="chat-turn chat-plan">
      <p className="eyebrow">Waiting for you</p>
      {plan.say && <p>{plan.say}</p>}

      <ul className="plan-ops">
        {operations.map((operation, index) => (
          <li key={index}>
            <span className="plan-op">{operation.summary}</span>
            {operation.before && operation.after && (
              <span className="plan-diff">
                <s>{operation.before}</s> → <b>{operation.after}</b>
              </span>
            )}
          </li>
        ))}
      </ul>

      {plan.stale && (
        <p className="notice broken">
          That dashboard changed after this was written, so it cannot be
          applied. Ask again and I will plan against what is there now.
        </p>
      )}

      <div className="plan-actions">
        <button
          type="button"
          className="primary"
          disabled={busy || plan.stale}
          onClick={onConfirm}
        >
          {plan.stale ? "Out of date" : "Apply"}
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          Discard
        </button>
      </div>
    </li>
  );
}
