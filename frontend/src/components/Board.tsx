import { useCallback, useEffect, useMemo, useState } from "react";
import GridLayout from "react-grid-layout";
import type { Board as BoardT, Layout, Providers } from "../api/client";
import { api } from "../api/client";
import Card from "./Card";

const COLS = 12;
const ROW_HEIGHT = 32;

export default function Board({
  boardId,
  examples,
  providers,
}: {
  boardId: string;
  examples: string[];
  providers: Providers;
}) {
  const [board, setBoard] = useState<BoardT | null>(null);
  const [width, setWidth] = useState(() => window.innerWidth - 40);

  const load = useCallback(async () => {
    const b = await api.getBoard(boardId);
    // Cards arrive without their render; fetch each so the trust surface
    // is populated the same way on first paint as after a refresh.
    const cards = await Promise.all(b.cards.map((c) => api.getCard(c.id)));
    setBoard({ ...b, cards });
  }, [boardId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth - 40);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const layout = useMemo(
    () =>
      (board?.cards ?? []).map((c) => ({
        i: c.id,
        x: c.layout?.x ?? 0,
        y: c.layout?.y ?? 0,
        w: c.layout?.w ?? 6,
        h: c.layout?.h ?? 9,
        minW: 3,
        minH: 6,
      })),
    [board],
  );

  const persist = async (next: { i: string; x: number; y: number; w: number; h: number }[]) => {
    const map: Record<string, Layout> = {};
    next.forEach((l) => (map[l.i] = { x: l.x, y: l.y, w: l.w, h: l.h }));
    await api.saveLayout(boardId, map);
  };

  if (!board) return <p className="eyebrow" style={{ padding: "1rem 1.25rem" }}>Loading…</p>;

  return (
    <div className="board-area">
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.75rem" }}>
        <button
          className="primary"
          onClick={async () => {
            await api.addCard(boardId);
            await load();
          }}
        >
          Add card
        </button>
      </div>

      {board.cards.length === 0 ? (
        <p className="eyebrow">No cards yet. Add one to ask a question.</p>
      ) : (
        <GridLayout
          className="layout"
          layout={layout}
          cols={COLS}
          rowHeight={ROW_HEIGHT}
          width={width}
          draggableHandle=".drag-handle"
          onDragStop={persist}
          onResizeStop={persist}
        >
          {board.cards.map((c) => (
            <div key={c.id}>
              <Card card={c} examples={examples} providers={providers} onChanged={load} />
            </div>
          ))}
        </GridLayout>
      )}
    </div>
  );
}
