import { useEffect, useState } from "react";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "./styles.css";
import Board from "./components/Board";
import type { Providers } from "./api/client";
import { api } from "./api/client";

export default function App() {
  const [boardId, setBoardId] = useState<string | null>(null);
  const [examples, setExamples] = useState<string[]>([]);
  const [providers, setProviders] = useState<Providers>({
    default: "anthropic",
    available: [],
  });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [boards, layer] = await Promise.all([api.listBoards(), api.layer()]);
        setExamples(layer.examples);
        setProviders(layer.providers);
        const board = boards[0] ?? (await api.createBoard("Operations"));
        setBoardId(board.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  return (
    <>
      <header className="masthead">
        <h1>Semantic Dashboard</h1>
        <span className="eyebrow">grounded in a curated layer · no row data leaves the warehouse</span>
        <span className="spacer" />
      </header>
      {error && <p className="notice broken" style={{ margin: "1rem 1.25rem" }}>{error}</p>}
      {boardId && <Board boardId={boardId} examples={examples} providers={providers} />}
    </>
  );
}
