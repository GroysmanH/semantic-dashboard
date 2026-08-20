import { useState } from "react";

/** The input, shared by an empty card and a ready one.
 *
 *  Same control, different job: on an empty card it asks a question, on a
 *  ready one it edits the chart that is already there. Keeping it one
 *  component is what stops the two drifting apart. */
export default function AskBar({
  placeholder,
  submitLabel,
  busy,
  onSubmit,
}: {
  placeholder: string;
  submitLabel: string;
  busy: boolean;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState("");

  return (
    <form
      className="ask"
      onSubmit={(e) => {
        e.preventDefault();
        if (!text.trim() || busy) return;
        onSubmit(text.trim());
        setText("");
      }}
    >
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        disabled={busy}
      />
      <button className="primary" type="submit" disabled={busy || !text.trim()}>
        {busy ? "…" : submitLabel}
      </button>
    </form>
  );
}
