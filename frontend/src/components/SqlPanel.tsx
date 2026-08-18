/** Collapsed by default: available to anyone who wants it, never the thing
 *  the manager has to read to trust the card. */
export default function SqlPanel({ sql }: { sql: string }) {
  return (
    <details className="sql">
      <summary className="eyebrow">Compiled SQL</summary>
      <pre>{sql}</pre>
    </details>
  );
}
