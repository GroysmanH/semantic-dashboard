import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { boardPng } from "../export/board";
import type { ChartSnapshot } from "../export/chart";
import { dashboardBlob } from "../export/json";
import { downloadBlob, safeName } from "../export/png";
import GridLayout from "react-grid-layout";
import type { Board as BoardT, Layout, Provider } from "../api/client";
import { api, LayoutConflictError } from "../api/client";
import Card from "./Card";
import ExportMenu from "./ExportMenu";

const COLS = 12;
const ROW_HEIGHT = 32;
const GRID_GAP = 10;
const DEFAULT_CARD_HEIGHT = 10;

type PanelKind = "edit" | "sql";
type PanelHeights = Record<string, { edit: number; sql: number }>;
type GridItem = Layout & { i: string };
type ActiveDrag = { id: string; origin: Layout; revision: number };
type ActiveResize = { id: string; revision: number };
type GestureIntent = {
  cardId: string;
  kind: "drag" | "resize";
  layout: Layout;
};
type PendingFocus = { id: string; target: "question" | "edit" | "card" };
export type CardNavigationTarget = {
  cardId: string;
  intent: "reveal" | "edit";
};

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

function gridItems(layouts: Record<string, Layout>): GridItem[] {
  return Object.entries(layouts).map(([i, item]) => ({ i, ...item }));
}

function packUpward(layouts: Record<string, Layout>): Record<string, Layout> {
  const placed: Layout[] = [];
  const packed: Record<string, Layout> = {};
  Object.entries(layouts)
    .sort(([leftId, left], [rightId, right]) => (
      left.y - right.y || left.x - right.x || leftId.localeCompare(rightId)
    ))
    .forEach(([id, desired]) => {
      let candidate = { ...desired, y: 0 };
      while (true) {
        const collisions = placed.filter((other) => overlaps(candidate, other));
        if (!collisions.length) break;
        candidate = {
          ...candidate,
          y: Math.max(...collisions.map((other) => other.y + other.h)),
        };
      }
      packed[id] = candidate;
      placed.push(candidate);
    });
  return packed;
}

function upwardGridPreview(
  items: GridItem[],
  canonical: Record<string, Layout>,
  preserveCanonicalSize: boolean,
): GridItem[] {
  const proposed: Record<string, Layout> = {};
  items.forEach(({ i, x, y, w, h }) => {
    const base = canonical[i];
    proposed[i] = preserveCanonicalSize && base
      ? { x, y, w: base.w, h: base.h }
      : { x, y, w, h };
  });
  const packed = packUpward(proposed);
  return items.map((item) => ({ ...item, ...packed[item.i] }));
}

function scrollBehavior(): ScrollBehavior {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
}

export default function Board({
  boardId,
  examples,
  provider,
  providers,
  strongAvailable,
  onProviderChange,
  onSelectionChange,
  cardTarget,
  onCardTargetHandled,
}: {
  boardId: string;
  examples: string[];
  provider: Provider;
  providers: Provider[];
  strongAvailable: boolean;
  onProviderChange: (p: Provider) => void;
  onSelectionChange?: (cardId: string | null) => void;
  cardTarget?: CardNavigationTarget | null;
  onCardTargetHandled?: () => void;
}) {
  const [board, setBoard] = useState<BoardT | null>(null);
  const [canonicalLayout, commitCanonicalLayout] = useState<Record<string, Layout>>({});
  const [panelHeights, setPanelHeights] = useState<PanelHeights>({});
  const [selectedCardId, setSelectedCardId] = useState<string | null>(null);
  const [editingCardId, setEditingCardId] = useState<string | null>(null);
  // Every ready visual card registers a snapshot path. The card itself
  // decides whether its current mounted view is valid or must be rebuilt.
  const snapshotsRef = useRef<Map<string, ChartSnapshot | null>>(new Map());
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [openSqlCardId, setOpenSqlCardId] = useState<string | null>(null);
  const [pendingFocus, setPendingFocus] = useState<PendingFocus | null>(null);
  const [adding, setAdding] = useState(false);
  const [changingLayoutMode, setChangingLayoutMode] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [resizingCardId, setResizingCardId] = useState<string | null>(null);
  const [boardError, setBoardError] = useState<string | null>(null);
  const [width, setWidth] = useState(() => window.innerWidth - 40);
  const areaRef = useRef<HTMLDivElement>(null);
  const cardNodes = useRef(new Map<string, HTMLDivElement>());
  const didDrag = useRef(false);
  const activeDrag = useRef<ActiveDrag | null>(null);
  const activeResize = useRef<ActiveResize | null>(null);
  const suppressSelectionUntil = useRef(0);
  const revisionRef = useRef(0);
  const canonicalLayoutRef = useRef<Record<string, Layout>>({});
  const layoutModeOperation = useRef(0);

  const setCanonicalLayout = useCallback((
    update: Record<string, Layout>
      | ((current: Record<string, Layout>) => Record<string, Layout>),
  ) => {
    const next = typeof update === "function"
      ? update(canonicalLayoutRef.current)
      : update;
    canonicalLayoutRef.current = next;
    commitCanonicalLayout(next);
  }, []);

  const load = useCallback(async () => {
    try {
      const next = await api.getBoard(boardId);
      if (next.revision < revisionRef.current) return true;
      const cards = await Promise.all(next.cards.map(async (snapshot) => {
        try {
          const hydrated = await api.getCard(snapshot.id);
          return { ...snapshot, ...hydrated, layout: snapshot.layout };
        } catch {
          return snapshot;
        }
      }));
      const hydrated = { ...next, cards };
      if (next.revision < revisionRef.current) return true;
      revisionRef.current = next.revision;
      setBoard(hydrated);
      setCanonicalLayout(layoutMap(next.cards));
      setBoardError(null);
      return true;
    } catch (error) {
      setBoardError(error instanceof Error ? error.message : String(error));
      return false;
    }
  }, [boardId, setCanonicalLayout]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    onSelectionChange?.(selectedCardId);
  }, [onSelectionChange, selectedCardId]);

  useEffect(() => {
    if (!cardTarget
        || !board?.cards.some((card) => card.id === cardTarget.cardId)) return;
    setSelectedCardId(cardTarget.cardId);
    setEditingCardId(cardTarget.intent === "edit" ? cardTarget.cardId : null);
    setPendingFocus({
      id: cardTarget.cardId,
      target: cardTarget.intent === "edit" ? "edit" : "card",
    });
    onCardTargetHandled?.();
  }, [board, cardTarget, onCardTargetHandled]);

  // Measured from the element, not the window. The two are not the same
  // number the moment anything else takes horizontal space: pinning the
  // assistant narrows this container by CSS alone, which fires no resize
  // event, so the grid kept the full window width and laid the cards out
  // underneath the panel. Dragging the panel's edge has the same problem
  // and now reflows live.
  useEffect(() => {
    const area = areaRef.current;
    const fromWindow = () => setWidth(window.innerWidth - 40);
    if (!area || typeof ResizeObserver === "undefined") {
      fromWindow();
      window.addEventListener("resize", fromWindow);
      return () => window.removeEventListener("resize", fromWindow);
    }
    const observer = new ResizeObserver(([entry]) => {
      // The padding is the board area's own; the grid gets what is left.
      const style = getComputedStyle(entry.target as Element);
      const inset = parseFloat(style.paddingLeft || "0")
        + parseFloat(style.paddingRight || "0");
      setWidth(Math.max(240, entry.contentRect.width + inset - 40));
    });
    observer.observe(area);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Element;
      if (target.closest("[data-card-id], .add-card-primary")) return;
      setSelectedCardId(null);
      setEditingCardId(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setSelectedCardId(null);
      setEditingCardId(null);
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
    if (editingCardId && !ids.has(editingCardId)) setEditingCardId(null);
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
  }, [board, editingCardId, openSqlCardId, selectedCardId]);

  useEffect(() => {
    if (!pendingFocus || !board?.cards.some((card) => card.id === pendingFocus.id)) return;
    let timeout: number | undefined;
    const frame = requestAnimationFrame(() => {
      // Recovery can be initiated by a button that remains mounted in the
      // chat drawer. Defer past that click's own focus assignment before
      // moving the caret into the newly opened card strip.
      const focusWhenMounted = (attempt: number) => {
        const registered = cardNodes.current.get(pendingFocus.id);
        const node = registered?.isConnected
          ? registered
          : document.querySelector<HTMLElement>(
            `[data-card-id="${pendingFocus.id}"]`,
          );
        const target = pendingFocus.target === "question"
          ? node?.querySelector<HTMLInputElement>('input[aria-label="Ask for a chart…"]')
          : pendingFocus.target === "edit"
            ? node?.querySelector<HTMLInputElement>('input[aria-label="Change this chart…"]')
            : node?.querySelector<HTMLElement>("article");
        if (!target && attempt < 4) {
          timeout = window.setTimeout(() => focusWhenMounted(attempt + 1), 0);
          return;
        }
        if (!target) return;
        target.focus({ preventScroll: true });
        target.scrollIntoView({ behavior: scrollBehavior(), block: "center" });
        setPendingFocus(null);
      };
      timeout = window.setTimeout(() => focusWhenMounted(0), 0);
    });
    return () => {
      cancelAnimationFrame(frame);
      if (timeout !== undefined) window.clearTimeout(timeout);
    };
  }, [board, pendingFocus]);

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
    if (card.id !== editingCardId) return false;
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

  const exportImage = async () => {
    if (!board) return;
    setExporting(true);
    setExportError(null);
    try {
      const entries = board.cards.map((card) => ({
        card,
        snapshot: snapshotsRef.current.get(card.id) ?? null,
      }));
      downloadBlob(await boardPng(board.title, entries), safeName(board.title, "png"));
    } catch (error) {
      setExportError(error instanceof Error ? error.message : String(error));
    } finally {
      setExporting(false);
    }
  };

  const exportJson = () => {
    if (!board) return;
    downloadBlob(dashboardBlob(board.title, board.cards), safeName(board.title, "json"));
  };

  const applyCanonical = useCallback((result: {
    layouts: Record<string, Layout>;
    revision: number;
  }) => {
    if (result.revision < revisionRef.current) return;
    revisionRef.current = result.revision;
    setCanonicalLayout(result.layouts);
    setBoard((current) => current && ({
      ...current,
      revision: result.revision,
      cards: current.cards.map((card) => ({
        ...card,
        layout: result.layouts[card.id] ?? card.layout,
      })),
    }));
  }, [setCanonicalLayout]);

  const persist = async (
    next: GridItem[],
    preserveCanonicalSize = false,
    manuallyResized: string[] = [],
    intent?: GestureIntent,
  ) => {
    const expectedRevision = revisionRef.current;
    const nextMap: Record<string, Layout> = {};
    next.forEach(({ i, x, y, w, h }) => {
      const canonical = canonicalLayoutRef.current[i];
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
      const result = await api.saveLayout(
        boardId, nextMap, manuallyResized, expectedRevision,
      );
      applyCanonical(result);
      setBoardError(null);
    } catch (error) {
      if (error instanceof LayoutConflictError) {
        applyCanonical(error);
        const current = error.layouts[intent?.cardId ?? ""];
        if (!intent || !current) {
          setBoardError(error.message);
          await load();
          return;
        }
        const target = intent.kind === "drag"
          ? { ...current, x: intent.layout.x, y: intent.layout.y }
          : { ...current, w: intent.layout.w, h: intent.layout.h };
        const retryMap = { ...error.layouts, [intent.cardId]: target };
        setCanonicalLayout(retryMap);
        try {
          const retried = await api.saveLayout(
            boardId, retryMap, manuallyResized, error.revision,
          );
          applyCanonical(retried);
          setBoardError(null);
          await load();
        } catch (retryError) {
          if (retryError instanceof LayoutConflictError) {
            applyCanonical(retryError);
            setBoardError(retryError.message);
          } else {
            setBoardError(
              retryError instanceof Error ? retryError.message : String(retryError),
            );
          }
          await load();
        }
        return;
      }
      setBoardError(error instanceof Error ? error.message : String(error));
      await load();
    }
  };

  const toggleSql = (cardId: string) => {
    setOpenSqlCardId((current) => (current === cardId ? null : cardId));
  };

  const prepareMove = useCallback(() => {
    setSelectedCardId(null);
    setEditingCardId(null);
    setOpenSqlCardId(null);
    setPanelHeights({});
  }, []);

  const selectCard = (cardId: string) => {
    if (Date.now() < suppressSelectionUntil.current) return;
    setSelectedCardId(cardId);
    setEditingCardId(cardId);
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
      setEditingCardId(created.id);
      setPendingFocus({ id: created.id, target: "question" });
    } catch (error) {
      setBoardError(error instanceof Error ? error.message : String(error));
    } finally {
      setAdding(false);
    }
  };

  const duplicateCard = async (cardId: string) => {
    setBoardError(null);
    const result = await api.duplicateCard(cardId);
    const hydratedCopy = await api.getCard(result.card.id).catch(() => result.card);
    applyCanonical(result);
    setBoard((current) => current && (
      current.cards.some((card) => card.id === result.card.id)
        ? current
        : {
            ...current,
            cards: [
              ...current.cards,
              { ...hydratedCopy, layout: result.layouts[result.card.id] ?? result.card.layout },
            ],
          }
    ));
    await load();
    setSelectedCardId(result.card.id);
    setEditingCardId(result.card.id);
    setPendingFocus({ id: result.card.id, target: "card" });
  };

  const setLayoutMode = async (enabled: boolean) => {
    if (!board || changingLayoutMode) return;
    const operation = ++layoutModeOperation.current;
    const startedRevision = revisionRef.current;
    const mode = enabled ? "auto_pack" : "free";
    const previousMode = board.layout_mode;
    const previousLayout = canonicalLayoutRef.current;
    const preview = enabled ? packUpward(previousLayout) : previousLayout;
    setChangingLayoutMode(true);
    setBoardError(null);
    setBoard((current) => current && ({
      ...current,
      layout_mode: mode,
      cards: current.cards.map((card) => ({
        ...card,
        layout: preview[card.id] ?? card.layout,
      })),
    }));
    if (enabled) setCanonicalLayout(preview);
    try {
      const updated = await api.updateBoard(boardId, { layout_mode: mode });
      if (updated.revision >= revisionRef.current) {
        revisionRef.current = updated.revision;
        setBoard((current) => current && ({ ...current, ...updated, cards: current.cards }));
      }
      await load();
    } catch (error) {
      if (operation === layoutModeOperation.current) {
        if (revisionRef.current === startedRevision) {
          setCanonicalLayout(previousLayout);
          setBoard((current) => current && ({
            ...current,
            layout_mode: previousMode,
            cards: current.cards.map((card) => ({
              ...card,
              layout: previousLayout[card.id] ?? card.layout,
            })),
          }));
        } else {
          setBoard((current) => current && ({ ...current, layout_mode: previousMode }));
        }
        const message = error instanceof Error ? error.message : String(error);
        await load();
        setBoardError(message);
      }
    } finally {
      if (operation === layoutModeOperation.current) setChangingLayoutMode(false);
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
    <div ref={areaRef}
         className={`board-area ${layoutLocked ? "layout-locked" : ""}`}>
      {boardError && <p className="notice broken board-error">{boardError}</p>}
      {exportError && <p className="notice broken board-error">{exportError}</p>}

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
          allowOverlap={dragging && !changingLayoutMode}
          draggableHandle=".drag-handle"
          isDraggable={!changingLayoutMode}
          isResizable={!layoutLocked && !changingLayoutMode}
          onDragStart={(_next, oldItem) => {
            didDrag.current = false;
            setDragging(true);
            activeDrag.current = {
              id: oldItem.i,
              origin: canonicalLayoutRef.current[oldItem.i]
                ?? { x: oldItem.x, y: oldItem.y, w: oldItem.w, h: oldItem.h },
              revision: revisionRef.current,
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
            const active = activeDrag.current;
            const resolveDrop = (current: Record<string, Layout>) => {
              const activeItem = active && raw.find((item) => item.i === active.id);
              const proposed = active && activeItem
                ? gridItems(current).map((item) => (
                    item.i === active.id
                      ? { ...item, x: activeItem.x, y: activeItem.y }
                      : item
                  ))
                : raw;
              const currentActive = active && current[active.id];
              const settledDrag = active && currentActive && active.revision !== revisionRef.current
                ? { ...active, origin: currentActive }
                : active;
              const settled = board.layout_mode === "auto_pack"
                ? upwardGridPreview(proposed, current, true)
                : settleDragLayout(proposed, settledDrag, current);
              return { proposed, settled };
            };
            const current = canonicalLayoutRef.current;
            const { proposed, settled } = resolveDrop(current);
            activeDrag.current = null;
            const requiresVisualReset = settled.some((item) => {
              const rawItem = raw.find((candidate) => candidate.i === item.i);
              return rawItem && (rawItem.x !== item.x || rawItem.y !== item.y);
            });
            if (!requiresVisualReset) {
              const activeItem = active && settled.find((item) => item.i === active.id);
              void persist(settled, true, [], active && activeItem ? {
                cardId: active.id,
                kind: "drag",
                layout: activeItem,
              } : undefined);
              return;
            }

            // RGL retains its internally displaced coordinates when the
            // collapsed prop layout is numerically unchanged. Give it one
            // frame at its raw drop layout, then animate to the resolved
            // canonical layout so transient panel movement cannot stick.
            const rawMap: Record<string, Layout> = {};
            proposed.forEach(({ i, x, y, w, h }) => {
              const base = current[i];
              rawMap[i] = { x, y, w: base?.w ?? w, h: base?.h ?? h };
            });
            // This intermediate layout exists only to reset RGL's internal
            // coordinates. Keep the authoritative ref intact, then rebuild
            // the request in the next frame in case a newer revision arrived.
            commitCanonicalLayout(rawMap);
            requestAnimationFrame(() => {
              const latest = resolveDrop(canonicalLayoutRef.current).settled;
              const activeItem = active && latest.find((item) => item.i === active.id);
              void persist(latest, true, [], active && activeItem ? {
                cardId: active.id,
                kind: "drag",
                layout: activeItem,
              } : undefined);
            });
          }}
          onResizeStart={(_next, _oldItem, newItem) => {
            activeResize.current = { id: newItem.i, revision: revisionRef.current };
            setResizingCardId(newItem.i);
          }}
          onResizeStop={(next) => {
            const resized = activeResize.current;
            activeResize.current = null;
            setResizingCardId(null);
            const raw = next as GridItem[];
            const current = canonicalLayoutRef.current;
            const resizedItem = resized && raw.find((item) => item.i === resized.id);
            const proposal = resized && resizedItem
              ? gridItems(current).map((item) => {
                  if (item.i !== resized.id) return item;
                  return resized.revision === revisionRef.current
                    ? { ...item, x: resizedItem.x, y: resizedItem.y, w: resizedItem.w, h: resizedItem.h }
                    : { ...item, w: resizedItem.w, h: resizedItem.h };
                })
              : raw;
            const preview = board.layout_mode === "auto_pack"
              ? upwardGridPreview(proposal, current, false)
              : proposal;
            const activeItem = resized && preview.find((item) => item.i === resized.id);
            void persist(
              preview,
              false,
              resized ? [resized.id] : [],
              resized && activeItem ? {
                cardId: resized.id,
                kind: "resize",
                layout: activeItem,
              } : undefined,
            );
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
                provider={provider}
                providers={providers}
                strongAvailable={strongAvailable}
                onProviderChange={onProviderChange}
                selected={selectedCardId === card.id}
                editing={editingCardId === card.id}
                sqlOpen={openSqlCardId === card.id}
                resizing={resizingCardId === card.id}
                onSelect={() => selectCard(card.id)}
                onMoveIntent={prepareMove}
                onSqlToggle={() => toggleSql(card.id)}
                onTransientHeight={reportTransientHeight}
                onDuplicate={duplicateCard}
                onChanged={() => void load()}
                onSnapshot={(snapshot) => snapshotsRef.current.set(card.id, snapshot)}
              />
            </div>
          ))}
        </GridLayout>
      )}

      {(() => {
        const autoPack = board.layout_mode === "auto_pack";
        const primaryAction = (
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
        const exportAction = (
          <div className="board-secondary-controls">
            <label className="auto-pack-control">
              <span className="auto-pack-label">Auto-pack</span>
              <input
                type="checkbox"
                role="switch"
                aria-label="Auto-pack"
                checked={autoPack}
                disabled={changingLayoutMode}
                onChange={(event) => void setLayoutMode(event.currentTarget.checked)}
              />
              <span className="auto-pack-track" aria-hidden="true" />
              <span className="auto-pack-state" aria-hidden="true">{autoPack ? "On" : "Off"}</span>
              <span className="sr-only" role="status">Auto-pack {autoPack ? "on" : "off"}</span>
            </label>
            <ExportMenu
              label={`Export ${board.title}`}
              items={[
                {
                  label: exporting ? "PNG (exporting…)" : "PNG",
                  disabled: exporting || !board.cards.length,
                  run: exportImage,
                },
                { label: "JSON", disabled: !board.cards.length, run: exportJson },
              ]}
              onError={(error) => setExportError(error instanceof Error ? error.message : String(error))}
            />
          </div>
        );
        const primaryHost = document.getElementById("board-primary-action");
        const exportHost = document.getElementById("board-export-action");
        return (
          <>
            {primaryHost ? createPortal(primaryAction, primaryHost) : primaryAction}
            {exportHost ? createPortal(exportAction, exportHost) : exportAction}
          </>
        );
      })()}
    </div>
  );
}
