import { useState } from "react";
import type { Provider } from "../api/client";
import AskBar from "./AskBar";
import Exchange, { replyPrompt, type ExchangeNote } from "./Exchange";
import ProviderPicker from "./ProviderPicker";

/** A real persisted row, not a placeholder: a manager sketching a layout
 *  with four blank cards must not lose them on reload. The examples are
 *  drawn from the layer, so this doubles as the discoverability mechanism
 *  for what vocabulary exists. */
export default function EmptyCard({
  examples,
  provider,
  providers,
  strongAvailable,
  busy,
  exchange,
  onProviderChange,
  onDismissNote,
  onAsk,
}: {
  examples: string[];
  provider: Provider;
  providers: Provider[];
  /** The provider itself is chosen once, for the session, in the assistant
   *  drawer. Repeating that choice on every blank card asked the same
   *  question four times on a four-card board. */
  strongAvailable: boolean;
  busy: boolean;
  exchange?: ExchangeNote | null;
  /** Shared with the assistant: the model is a session choice, so picking
   *  one here changes it there too. */
  onProviderChange: (p: Provider) => void;
  onDismissNote: () => void;
  onAsk: (question: string, hard: boolean, reply: boolean) => void;
}) {
  const [hard, setHard] = useState(false);
  // Whatever is typed while the card is mid-exchange is a reply to it, so
  // the one composer says so rather than a second one appearing beneath.
  const open = exchange ?? null;
  const prompt = open
    ? replyPrompt(open)
    : { placeholder: "Ask for a chart…", label: "Ask" };

  const submit = (q: string) => {
    if (!q.trim() || busy) return;
    onAsk(q.trim(), hard, open !== null);
  };

  return (
    <div className="empty-body">
      {open && <Exchange note={open} busy={busy} onDismiss={onDismissNote} />}

      <AskBar
        placeholder={prompt.placeholder}
        submitLabel={prompt.label}
        busy={busy}
        onSubmit={submit}
      />

      <ProviderPicker
        provider={provider}
        providers={providers}
        busy={busy}
        onChange={onProviderChange}
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

      {/* Not while an exchange is open: the composer above is a reply box,
          and an example sent into it would answer the card's question with
          a sentence that was never meant for it. */}
      {!open && (
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
