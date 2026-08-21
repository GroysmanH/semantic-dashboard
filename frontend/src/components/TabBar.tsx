import { useEffect, useMemo, useRef, useState } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
} from "@dnd-kit/sortable";
import type { BoardSummary } from "../api/client";
import SortableTab from "./SortableTab";

function PlusIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M9.25 4h1.5v5.25H16v1.5h-5.25V16h-1.5v-5.25H4v-1.5h5.25V4Z" />
    </svg>
  );
}

/** Tabs over the boards that already existed in the schema and were never
 *  more than one. Renaming is inline rather than a dialog: a tab is its
 *  name, and editing it in place is the shortest path between the two. */
export default function TabBar({
  boards,
  activeId,
  busy,
  onSelect,
  onCreate,
  onRename,
  onDelete,
  onReorder,
}: {
  boards: BoardSummary[];
  activeId: string | null;
  busy: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onReorder: (order: string[]) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const announcements = useMemo<Announcements>(() => {
    const titleOf = (id: string | number) => (
      boards.find((board) => board.id === String(id))?.title || "Untitled"
    );
    const positionOf = (id: string | number) => (
      boards.findIndex((board) => board.id === String(id)) + 1
    );
    return {
      onDragStart: ({ active }) => `Picked up ${titleOf(active.id)} dashboard.`,
      onDragOver: ({ active, over }) => over
        ? `${titleOf(active.id)} dashboard is in position ${positionOf(over.id)} of ${boards.length}.`
        : `${titleOf(active.id)} dashboard is not over a drop position.`,
      onDragEnd: ({ active, over }) => over
        ? `Moved ${titleOf(active.id)} dashboard to position ${positionOf(over.id)} of ${boards.length}.`
        : `${titleOf(active.id)} dashboard was not moved.`,
      onDragCancel: ({ active }) => `Cancelled moving ${titleOf(active.id)} dashboard.`,
    };
  }, [boards]);

  useEffect(() => {
    if (editingId) inputRef.current?.select();
  }, [editingId]);

  const startRename = (board: BoardSummary) => {
    if (busy) return;
    setEditingId(board.id);
    setDraft(board.title);
  };

  const commit = () => {
    const id = editingId;
    if (!id) return;
    const title = draft.trim();
    setEditingId(null);
    // An empty name would leave a tab you cannot click. Keep the old one.
    const previous = boards.find((b) => b.id === id)?.title;
    if (title && title !== previous) onRename(id, title);
  };

  const finishSort = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return;
    const from = boards.findIndex((board) => board.id === active.id);
    const to = boards.findIndex((board) => board.id === over.id);
    if (from < 0 || to < 0) return;
    onReorder(arrayMove(boards, from, to).map((board) => board.id));
  };

  return (
    <nav className="tabbar" aria-label="Dashboards">
      <DndContext
        sensors={sensors}
        onDragEnd={finishSort}
        accessibility={{
          announcements,
          screenReaderInstructions: {
            draggable: "Press Space or Enter to pick up a dashboard, use arrow keys to move it, and press Space or Enter to drop. Press Escape to cancel.",
          },
        }}
      >
        <SortableContext
          items={boards.map((board) => board.id)}
          strategy={horizontalListSortingStrategy}
        >
          <ul className="tabbar-list" role="tablist">
            {boards.map((board) => {
              const active = board.id === activeId;
              const editing = editingId === board.id;
              return (
                <SortableTab
                  key={board.id}
                  id={board.id}
                  label={board.title || "Untitled"}
                  active={active}
                  disabled={busy || editing}
                >
                  {editing ? (
                    <input
                      ref={inputRef}
                      className="tab-rename"
                      aria-label={`Rename ${board.title}`}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={commit}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commit();
                        if (e.key === "Escape") setEditingId(null);
                      }}
                    />
                  ) : (
                    <>
                      <button
                        type="button"
                        role="tab"
                        aria-selected={active}
                        className="tab-label"
                        onClick={() => onSelect(board.id)}
                        onDoubleClick={() => startRename(board)}
                      >
                        {board.title || "Untitled"}
                      </button>
                      <button
                        type="button"
                        className="tab-close"
                        aria-label={`Delete ${board.title}`}
                        disabled={busy || boards.length < 2}
                        onClick={() => onDelete(board.id)}
                      >
                        ×
                      </button>
                    </>
                  )}
                </SortableTab>
              );
            })}
          </ul>
        </SortableContext>
      </DndContext>
      <button
        type="button"
        className="tab-add"
        aria-label="New dashboard"
        disabled={busy}
        onClick={onCreate}
      >
        <PlusIcon />
      </button>
    </nav>
  );
}
