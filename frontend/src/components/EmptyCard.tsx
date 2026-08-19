import { useState } from "react";
import type { Provider, Providers } from "../api/client";

const PROVIDER_LABEL: Record<Provider, string> = {
  anthropic: "Claude",
  gemini: "Gemini",
};

/** A real persisted row, not a placeholder: a manager sketching a layout
 *  with four blank cards must not lose them on reload. The examples are
 *  drawn from the layer, so this doubles as the discoverability mechanism
 *  for what vocabulary exists. */
export default function EmptyCard({
  examples,
  providers,
  busy,
  note,
  onAsk,
}: {
  examples: string[];
  providers: Providers;
  busy: boolean;
  note?: string | null;
  onAsk: (question: string, hard: boolean, provider: Provider) => void;
}) {
  const [text, setText] = useState("");
  const [hard, setHard] = useState(false);
  const [provider, setProvider] = useState<Provider>(providers.default);

  const submit = (q: string) => {
    if (!q.trim() || busy) return;
    onAsk(q.trim(), hard, provider);
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

      {/* Only shown when there is a real choice. One key configured is not
          a decision worth putting in front of anyone. */}
      {providers.available.length > 1 && (
        <div className="provider" role="radiogroup" aria-label="Model provider">
          <span className="eyebrow">Ask</span>
          {providers.available.map((p) => (
            <button
              key={p}
              type="button"
              role="radio"
              aria-checked={provider === p}
              className={provider === p ? "on" : ""}
              onClick={() => setProvider(p)}
              disabled={busy}
            >
              {PROVIDER_LABEL[p] ?? p}
            </button>
          ))}
        </div>
      )}

      <label className="harder" title="Costs more. Use it when a question needs care.">
        <input
          type="checkbox"
          checked={hard}
          onChange={(e) => setHard(e.target.checked)}
          disabled={busy}
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
