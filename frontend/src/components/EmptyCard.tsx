import { useState } from "react";
import AskBar from "./AskBar";

/** A real persisted row, not a placeholder: a manager sketching a layout
 *  with four blank cards must not lose them on reload. The examples are
 *  drawn from the layer, so this doubles as the discoverability mechanism
 *  for what vocabulary exists. */
export default function EmptyCard({
  examples,
  strongAvailable,
  busy,
  note,
  onAsk,
}: {
  examples: string[];
  /** The provider itself is chosen once, for the session, in the assistant
   *  drawer. Repeating that choice on every blank card asked the same
   *  question four times on a four-card board. */
  strongAvailable: boolean;
  busy: boolean;
  note?: string | null;
  onAsk: (question: string, hard: boolean) => void;
}) {
  const [hard, setHard] = useState(false);

  const submit = (q: string) => {
    if (!q.trim() || busy) return;
    onAsk(q.trim(), hard);
  };

  return (
    <div className="empty-body">
      <AskBar
        placeholder="Ask for a chart…"
        submitLabel="Ask"
        busy={busy}
        onSubmit={submit}
      />

      <label
        className="harder"
        title={strongAvailable
          ? "Costs more. Use it when a question needs care."
          : "This provider has one model tier, so there is nothing to escalate to."}
      >
        <input
          type="checkbox"
          checked={hard && strongAvailable}
          onChange={(e) => setHard(e.target.checked)}
          disabled={busy || !strongAvailable}
        />
        <span>This one is hard — think harder about it</span>
      </label>

      {note && <div className="notice hint">{note}</div>}

      {(
        <div className="examples">
          <span className="eyebrow">Try</span>
          {examples.map((q) => (
            <button key={q} type="button" onClick={() => submit(q)} disabled={busy}>
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
