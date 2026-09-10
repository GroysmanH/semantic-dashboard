/** What the card is waiting on, and a way out of it.
 *
 *  A card that cannot answer says why -- "by 'top' do you mean oil or gas?",
 *  "the layer has no per-region ranking" -- and until now that sentence was
 *  shown as a notice and then forgotten. The next thing typed arrived at the
 *  model with no idea what it was responding to, so the person had to
 *  restate the whole request to make any progress.
 *
 *  Rendering it as one half of an exchange is what makes the other half
 *  possible: while this is on screen the composer below is a reply box, and
 *  the server is told to read what it sends as an answer to this. Walking
 *  away has to be one click, because a question the card asked is not an
 *  obligation -- somebody may simply want to ask something else. */
export type ExchangeNote = { kind: "clarify" | "refused"; text: string };

const EYEBROW: Record<"clarify" | "refused", string> = {
  clarify: "Needs an answer",
  refused: "Cannot answer that",
};

export function replyPrompt(note: ExchangeNote): { placeholder: string; label: string } {
  // A question is answered; a refusal is argued with. Naming the difference
  // in the placeholder is what tells somebody which one they are doing.
  return note.kind === "clarify"
    ? { placeholder: "Answer that…", label: "Reply" }
    : { placeholder: "Reply to that…", label: "Reply" };
}

export default function Exchange({
  note,
  busy,
  onDismiss,
}: {
  note: ExchangeNote;
  busy: boolean;
  onDismiss: () => void;
}) {
  const kind = note.kind === "refused" ? "refused" : "clarify";

  return (
    <div className={`exchange ${kind}`} role="status">
      <p className="eyebrow">{EYEBROW[kind]}</p>
      <p className="exchange-said">{note.text}</p>
      <button type="button" className="link" disabled={busy} onClick={onDismiss}>
        Ask something else
      </button>
    </div>
  );
}
