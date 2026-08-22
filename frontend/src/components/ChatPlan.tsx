import type { PendingPlanView, PlanOperation } from "../api/types.gen";

const REMOVES = new Set(["delete_card", "delete_dashboard"]);

/** What the button does, said on the button.
 *
 *  "Apply" is the same word for adding a card and for clearing a dashboard.
 *  A confirmation whose label does not distinguish those is a confirmation
 *  in name only — it stops being read after the second time. */
function label(operations: PlanOperation[]): string {
  if (operations.length === 0) return "Apply";
  const n = operations.length;
  const kinds = new Set(operations.map((o) => o.kind));
  if (kinds.has("create_dashboard")) return "Build it";
  if (kinds.has("delete_dashboard")) return "Remove the dashboard";
  if (kinds.has("delete_card")) {
    return n === 1 ? "Remove the card" : `Remove ${n} cards`;
  }
  if (kinds.has("create_card")) {
    return n === 1 ? "Add the card" : `Add ${n} cards`;
  }
  if (kinds.has("move_card")) return n === 1 ? "Move it" : `Move ${n} cards`;
  if (kinds.has("rename_dashboard")) return "Rename it";
  if (kinds.has("reorder_dashboards")) return "Reorder the tabs";
  if (kinds.has("edit_card")) return "Change the card";
  return "Apply";
}

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
  // A removal is the one kind of change worth colouring, because it is the
  // one whose preview a person most needs to have actually read.
  const removes = operations.some((o) => REMOVES.has(o.kind));

  return (
    <li className={removes ? "chat-turn chat-plan removes" : "chat-turn chat-plan"}>
      <p className="eyebrow">{removes ? "Removes things" : "Waiting for you"}</p>
      {plan.say && <p>{plan.say}</p>}

      <ul className="plan-ops">
        {operations.map((operation, index) => (
          <li key={index}
              className={REMOVES.has(operation.kind) ? "op-removes" : undefined}>
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
          className={removes ? "danger" : "primary"}
          disabled={busy || plan.stale}
          onClick={onConfirm}
        >
          {plan.stale ? "Out of date" : label(operations)}
        </button>
        <button type="button" disabled={busy} onClick={onCancel}>
          Discard
        </button>
      </div>
    </li>
  );
}
