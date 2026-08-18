import { useState } from "react";

/** A real persisted row, not a placeholder: a manager sketching a layout
 *  with four blank cards must not lose them on reload. The examples are
 *  drawn from the layer, so this doubles as the discoverability mechanism
 *  for what vocabulary exists. */
export default function EmptyCard({
  examples,
  busy,
  note,
  onAsk,
}: {
  examples: string[];
  busy: boolean;
  note?: string | null;
  onAsk: (question: string) => void;
}) {
  const [text, setText] = useState("");

  const submit = (q: string) => {
    if (!q.trim() || busy) return;
    onAsk(q.trim());
  };

  return (
    <div className="empty-body">
      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault();
          submit(text);
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Ask for a chart…"
          aria-label="Ask for a chart"
          disabled={busy}
        />
        <button className="primary" type="submit" disabled={busy || !text.trim()}>
          {busy ? "Asking…" : "Ask"}
        </button>
      </form>

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
