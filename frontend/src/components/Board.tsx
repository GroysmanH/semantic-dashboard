import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import GridLayout from "react-grid-layout";
import type { Board as BoardT, Layout, Providers } from "../api/client";
import { api } from "../api/client";
import Card from "./Card";

const COLS = 12;
const ROW_HEIGHT = 32;
const GRID_GAP = 10;
const DEFAULT_CARD_HEIGHT = 10;

type PanelKind = "edit" | "sql";
type PanelHeights = Record<string, { edit: number; sql: number }>;
type GridItem = Layout & { i: string };
type ActiveDrag = { id: string; origin: Layout };

function overlaps(a: Layout, b: Layout): boolean {
  return a.x < b.x + b.w
    && a.x + a.w > b.x
    && a.y < b.y + b.h
    && a.y + a.h > b.y;
}

function settleDragLayout(
  next: GridItem[],
  active: ActiveDrag | null,
  canonical: Record<string, Layout>,
): GridItem[] {
  if (!active) return next;
  const normalized = next.map((item) => {
    const base = canonical[item.i];
    if (!base) return { ...item };
    if (item.i === active.id) return { ...item, w: base.w, h: base.h };
    return { ...item, ...base };
  });
  const dragged = normalized.find((item) => item.i === active.id);
  if (!dragged) return next;

  const directlyDisplaced = normalized.filter(
    (item) => item.i !== active.id && overlaps(item, dragged),
  );
  if (directlyDisplaced.length === 0) return normalized;

  if (directlyDisplaced.length === 1) {
    const displaced = directlyDisplaced[0];
    const candidate = { ...displaced, x: active.origin.x, y: active.origin.y };
    const collisionAtOrigin = normalized.some(
      (item) => item.i !== displaced.i && overlaps(candidate, item),
    );
    if (candidate.x + candidate.w <= COLS && !collisionAtOrigin) {
      return normalized.map((item) => (item.i === displaced.i ? candidate : item));
    }
  }

  // During the drag every other card remains at its canonical position. On
  // drop, search the whole grid for the closest genuine opening instead of
  // relying on RGL's one-directional vertical compaction. Drag direction is
  // a gentle tie-breaker: pushing right tends to move an occupant right, but
  // a much closer opening in another direction still wins.
  const dragVector = {
    x: dragged.x - active.origin.x,
    y: dragged.y - active.origin.y,
  };
  const displacedIds = new Set(directlyDisplaced.map((item) => item.i));
  const placed = normalized
    .filter((item) => !displacedIds.has(item.i))
    .map((item) => ({ ...item }));
  const resolvedPositions = new Map<string, GridItem>();
  const maxBottom = Math.max(...normalized.map((item) => item.y + item.h), 0);

  const nearestOpening = (item: GridItem): GridItem => {
    const candidates: Array<GridItem & {
      score: number;
      distance: number;
      alignment: number;
    }> = [];
    const maxY = maxBottom + item.h + COLS;
    for (let y = 0; y <= maxY; y += 1) {
      for (let x = 0; x <= COLS - item.w; x += 1) {
        const candidate = { ...item, x, y };
        if (placed.some((obstacle) => overlaps(candidate, obstacle))) continue;
        const dx = x - item.x;
        const dy = y - item.y;
        const distance = Math.abs(dx) + Math.abs(dy);
        const dot = dx * dragVector.x + dy * dragVector.y;
        const directionPenalty = dot > 0 ? 0 : dot === 0 ? 2 : 4;
        const alignment = Math.abs(dx * dragVector.y - dy * dragVector.x);
        candidates.push({
          ...candidate,
          distance,
          alignment,
          score: distance + directionPenalty,
        });
      }
    }
    candidates.sort((a, b) => (
      a.score - b.score
      || a.distance - b.distance
      || a.alignment - b.alignment
      || Math.abs(a.y - item.y) - Math.abs(b.y - item.y)
      || a.y - b.y
      || a.x - b.x
    ));
    const best = candidates[0];
    if (best) {
      const {
        score: _score,
        distance: _distance,
        alignment: _alignment,
        ...position
      } = best;
      return position;
    }
    return { ...item, x: 0, y: maxBottom + GRID_GAP };
  };

  directlyDisplaced
    .sort((a, b) => a.y - b.y || a.x - b.x)
    .forEach((item) => {
      const position = nearestOpening({ ...item });
      placed.push(position);
      resolvedPositions.set(item.i, position);
    });

  return normalized.map((item) => resolvedPositions.get(item.i) ?? item);
}

function layoutMap(cards: BoardT["cards"]): Record<string, Layout> {
  return Object.fromEntries(cards.map((card) => [
    card.id,
    card.layout ?? { x: 0, y: 0, w: 6, h: DEFAULT_CARD_HEIGHT },
  ]));
}

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
  const [canonicalLayout, setCanonicalLayout] = useState<Record<string, Layout>>({});
  const [panelHeights, setPanelHeights] = useState<PanelHeights>({});
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null);
  const [openSqlCardId, setOpenSqlCardId] = useState<string | null>(null);
  const [pendingFocusId, setPendingFocusId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [resizingCardId, setResizingCardId] = useState<string | null>(null);
  const [boardError, setBoardError] = useState<string | null>(null);
  const [width, setWidth] = useState(() => window.innerWidth - 40);
  const cardNodes = useRef(new Map<string, HTMLDivElement>());
  const didDrag = useRef(false);
  const activeDrag = useRef<ActiveDrag | null>(null);
  const suppressSelectionUntil = useRef(0);

  const load = useCallback(async () => {
    try {
      const next = await api.getBoard(boardId);
      const cards = await Promise.all(next.cards.map((card) => api.getCard(card.id)));
      const hydrated = { ...next, cards };
      setBoard(hydrated);
      setCanonicalLayout(layoutMap(cards));
      setBoardError(null);
      return true;
    } catch (error) {
      setBoardError(error instanceof Error ? error.message : String(error));
      return false;
    }
  }, [boardId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onResize = () => setWidth(window.innerWidth - 40);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Element;
      if (target.closest("[data-card-id], .add-card-primary")) return;
      setSelectedCardId(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setSelectedCardId(null);
      setOpenSqlCardId(null);
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  useEffect(() => {
    if (!board) return;
    const ids = new Set(board.cards.map((card) => card.id));
    if (selectedCardId && !ids.has(selectedCardId)) setSelectedCardId(null);
    if (openSqlCardId) {
      const card = board.cards.find((candidate) => candidate.id === openSqlCardId);
      const state = card?.render?.state ?? card?.state;
      const unplottable = state === "broken"
        && card?.render?.error_reason === "unplottable";
      if (!card || !card.render?.compiled_sql || (state !== "ready" && !unplottable)) {
        setOpenSqlCardId(null);
        setPanelHeights((current) => {
          const previous = current[openSqlCardId];
          if (!previous?.sql) return current;
          const next = { ...current, [openSqlCardId]: { ...previous, sql: 0 } };
          if (next[openSqlCardId].edit === 0) delete next[openSqlCardId];
          return next;
        });
      }
    }
  }, [board, openSqlCardId, selectedCardId]);

  useEffect(() => {
    if (!pendingFocusId || !board?.cards.some((card) => card.id === pendingFocusId)) return;
    const frame = requestAnimationFrame(() => {
      const node = cardNodes.current.get(pendingFocusId);
      const input = node?.querySelector<HTMLInputElement>('input[aria-label="Ask for a chart…"]');
      input?.focus({ preventScroll: true });
      node?.scrollIntoView({ behavior: "smooth", block: "center" });
      setPendingFocusId(null);
    });
    return () => cancelAnimationFrame(frame);
  }, [board, pendingFocusId]);

  const reportTransientHeight = useCallback((cardId: string, kind: PanelKind, height: number) => {
    setPanelHeights((current) => {
      const previous = current[cardId] ?? { edit: 0, sql: 0 };
      if (previous[kind] === height) return current;
      const next = { ...current, [cardId]: { ...previous, [kind]: height } };
      if (next[cardId].edit === 0 && next[cardId].sql === 0) delete next[cardId];
      return next;
    });
  }, []);

  const layout = useMemo<GridItem[]>(() => (
    (board?.cards ?? []).map((card) => {
      const base = canonicalLayout[card.id]
        ?? card.layout
        ?? { x: 0, y: 0, w: 6, h: DEFAULT_CARD_HEIGHT };
      const panels = panelHeights[card.id] ?? { edit: 0, sql: 0 };
      const transientRows = Math.ceil((panels.edit + panels.sql) / (ROW_HEIGHT + GRID_GAP));
      return {
        i: card.id,
        ...base,
        h: base.h + transientRows,
        minW: 3,
        minH: 6 + transientRows,
      };
    })
  ), [board, canonicalLayout, panelHeights]);

  const selectedEditable = board?.cards.some((card) => {
    if (card.id !== selectedCardId) return false;
    const state = card.render?.state ?? card.state;
    return state === "ready"
      || (state === "broken" && card.render?.error_reason === "unplottable");
  });
  const openSqlEligible = board?.cards.some((card) => {
    if (card.id !== openSqlCardId || !card.render?.compiled_sql) return false;
    const state = card.render.state ?? card.state;
    return state === "ready"
      || (state === "broken" && card.render.error_reason === "unplottable");
  });
  const layoutLocked = Boolean(selectedEditable || openSqlEligible);

  const persist = async (next: GridItem[], preserveCanonicalSize = false) => {
    const nextMap: Record<string, Layout> = {};
    next.forEach(({ i, x, y, w, h }) => {
      const canonical = canonicalLayout[i];
      nextMap[i] = preserveCanonicalSize && canonical
        ? { x, y, w: canonical.w, h: canonical.h }
        : { x, y, w, h };
    });
    setCanonicalLayout(nextMap);
    setBoard((current) => current && ({
      ...current,
      cards: current.cards.map((card) => ({ ...card, layout: nextMap[card.id] ?? card.layout })),
    }));
    try {
      await api.saveLayout(boardId, nextMap);
      setBoardError(null);
    } catch (error) {
      setBoardError(error instanceof Error ? error.message : String(error));
      await load();
    }
  };

  const toggleSql = (cardId: string) => {
    setOpenSqlCardId((current) => (current === cardId ? null : cardId));
  };

  const prepareMove = useCallback(() => {
    setSelectedCardId(null);
    setOpenSqlCardId(null);
    setPanelHeights({});
  }, []);

  const selectCard = (cardId: string) => {
    if (Date.now() < suppressSelectionUntil.current) return;
    setSelectedCardId(cardId);
  };

  const addCard = async () => {
    setAdding(true);
    setBoardError(null);
    try {
      const created = await api.addCard(boardId);
      const reloaded = await load();
      if (!reloaded) {
        setBoard((current) => current && (
          current.cards.some((card) => card.id === created.id)
            ? current
            : { ...current, cards: [...current.cards, created] }
        ));
        setCanonicalLayout((current) => ({
          ...current,
          [created.id]: created.layout ?? { x: 0, y: 0, w: 6, h: DEFAULT_CARD_HEIGHT },
        }));
      }
      setSelectedCardId(created.id);
      setPendingFocusId(created.id);
    } catch (error) {
      setBoardError(error instanceof Error ? error.message : String(error));
    } finally {
      setAdding(false);
    }
  };

  if (!board) {
    return (
      <div className="board-area">
        {boardError ? <p className="notice broken board-error">{boardError}</p> : <p className="eyebrow">Loading…</p>}
      </div>
    );
  }

  return (
    <div className={`board-area ${layoutLocked ? "layout-locked" : ""}`}>
      {boardError && <p className="notice broken board-error">{boardError}</p>}

      {board.cards.length === 0 ? (
        <p className="eyebrow empty-board">No cards yet. Add one to ask a question.</p>
      ) : (
        <GridLayout
          className="layout"
          layout={layout}
          cols={COLS}
          rowHeight={ROW_HEIGHT}
          margin={[GRID_GAP, GRID_GAP]}
          containerPadding={[0, 0]}
          width={width}
          compactType={null}
          preventCollision={false}
          allowOverlap={dragging}
          draggableHandle=".drag-handle"
          isDraggable
          isResizable={!layoutLocked}
          onDragStart={(_next, oldItem) => {
            didDrag.current = false;
            setDragging(true);
            activeDrag.current = {
              id: oldItem.i,
              origin: canonicalLayout[oldItem.i]
                ?? { x: oldItem.x, y: oldItem.y, w: oldItem.w, h: oldItem.h },
            };
            prepareMove();
          }}
          onDrag={() => {
            didDrag.current = true;
          }}
          onDragStop={(next) => {
            setDragging(false);
            if (didDrag.current) suppressSelectionUntil.current = Date.now() + 250;
            const raw = next as GridItem[];
            const settled = settleDragLayout(
              raw,
              activeDrag.current,
              canonicalLayout,
            );
            activeDrag.current = null;
            const requiresVisualReset = settled.some((item) => {
              const rawItem = raw.find((candidate) => candidate.i === item.i);
              return rawItem && (rawItem.x !== item.x || rawItem.y !== item.y);
            });
            if (!requiresVisualReset) {
              void persist(settled, true);
              return;
            }

            // RGL retains its internally displaced coordinates when the
            // collapsed prop layout is numerically unchanged. Give it one
            // frame at its raw drop layout, then animate to the resolved
            // canonical layout so transient panel movement cannot stick.
            const rawMap: Record<string, Layout> = {};
            raw.forEach(({ i, x, y, w, h }) => {
              const base = canonicalLayout[i];
              rawMap[i] = { x, y, w: base?.w ?? w, h: base?.h ?? h };
            });
            setCanonicalLayout(rawMap);
            requestAnimationFrame(() => void persist(settled, true));
          }}
          onResizeStart={(_next, _oldItem, newItem) => setResizingCardId(newItem.i)}
          onResizeStop={(next) => {
            setResizingCardId(null);
            void persist(next as GridItem[]);
          }}
        >
          {board.cards.map((card) => (
            <div
              key={card.id}
              data-card-id={card.id}
              ref={(node) => {
                if (node) cardNodes.current.set(card.id, node);
                else cardNodes.current.delete(card.id);
              }}
            >
              <Card
                card={card}
                examples={examples}
                providers={providers}
                selected={selectedCardId === card.id}
                sqlOpen={openSqlCardId === card.id}
                resizing={resizingCardId === card.id}
                onSelect={() => selectCard(card.id)}
                onMoveIntent={prepareMove}
                onSqlToggle={() => toggleSql(card.id)}
                onTransientHeight={reportTransientHeight}
                onChanged={() => void load()}
              />
            </div>
          ))}
        </GridLayout>
      )}

      {(() => {
        const action = (
          <button
            className="add-card-primary"
            type="button"
            onClick={() => void addCard()}
            disabled={adding}
            aria-label="Add card"
            title="Add a new dashboard card"
          >
            <span className="add-card-plus" aria-hidden="true">{adding ? "…" : "+"}</span>
            <span>{adding ? "Adding…" : "New card"}</span>
          </button>
        );
        const host = document.getElementById("board-primary-action");
        return host ? createPortal(action, host) : action;
      })()}
    </div>
  );
}
