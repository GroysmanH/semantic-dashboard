import { useCallback, useEffect, useState } from "react";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "./styles.css";
import Board from "./components/Board";
import TabBar from "./components/TabBar";
import type { BoardSummary, Providers } from "./api/client";
import { api } from "./api/client";

// Which tab you were last on is view state, not data. It belongs to this
// browser, not to the boards everyone shares.
const LAST_BOARD = "semantic-dashboard:last-board";

export default function App() {
  const [boards, setBoards] = useState<BoardSummary[]>([]);
  const [boardId, setBoardId] = useState<string | null>(null);
  const [examples, setExamples] = useState<string[]>([]);
  const [providers, setProviders] = useState<Providers>({
    default: "anthropic",
    available: [],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const select = useCallback((id: string) => {
    setBoardId(id);
    try {
      localStorage.setItem(LAST_BOARD, id);
    } catch {
      // Private browsing and storage-blocked contexts: the tab still works,
      // it just will not be remembered.
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const [listed, layer] = await Promise.all([api.listBoards(), api.layer()]);
        setExamples(layer.examples);
        setProviders(layer.providers);

        const known = listed.length ? listed : [await api.createBoard("Operations")];
        setBoards(known);

        let remembered: string | null = null;
        try {
          remembered = localStorage.getItem(LAST_BOARD);
        } catch {
          remembered = null;
        }
        // A remembered board may since have been deleted, so fall back
        // rather than showing an empty screen.
        const opening = known.find((b) => b.id === remembered) ?? known[0];
        select(opening.id);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, [select]);

  const guard = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const create = () =>
    guard(async () => {
      const board = await api.createBoard("New dashboard");
      setBoards((prev) => [...prev, board]);
      select(board.id);
    });

  const rename = (id: string, title: string) =>
    guard(async () => {
      const updated = await api.updateBoard(id, { title });
      setBoards((prev) => prev.map((b) => (b.id === id ? updated : b)));
    });

  const remove = (id: string) =>
    guard(async () => {
      const board = boards.find((b) => b.id === id);
      const cards = board ? await api.getBoard(id) : null;
      const count = cards?.cards.length ?? 0;
      const warning = count
        ? `Delete "${board?.title}" and its ${count} card${count === 1 ? "" : "s"}? This cannot be undone.`
        : `Delete "${board?.title}"?`;
      if (!window.confirm(warning)) return;

      await api.deleteBoard(id);
      const left = boards.filter((b) => b.id !== id);
      setBoards(left);
      if (boardId === id && left.length) select(left[0].id);
    });

  return (
    <>
      <header className="masthead">
        <div id="board-primary-action" className="masthead-action" />
        <h1>Semantic Dashboard</h1>
        <span className="eyebrow">grounded in a curated layer · no row data leaves the warehouse</span>
        <span className="spacer" />
      </header>
      <TabBar
        boards={boards}
        activeId={boardId}
        busy={busy}
        onSelect={select}
        onCreate={create}
        onRename={rename}
        onDelete={remove}
      />
      {error && <p className="notice broken" style={{ margin: "1rem 1.25rem" }}>{error}</p>}
      {boardId && (
        <Board key={boardId} boardId={boardId} examples={examples} providers={providers} />
      )}
    </>
  );
}
