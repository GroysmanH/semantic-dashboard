import { useEffect, useRef, useState } from "react";
import type { BoardSummary } from "../api/client";

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
}: {
  boards: BoardSummary[];
  activeId: string | null;
  busy: boolean;
  onSelect: (id: string) => void;
  onCreate: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingId) inputRef.current?.select();
  }, [editingId]);

  const startRename = (board: BoardSummary) => {
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

  return (
    <nav className="tabbar" aria-label="Dashboards">
      <ul className="tabbar-list" role="tablist">
        {boards.map((board) => {
          const active = board.id === activeId;
          if (editingId === board.id) {
            return (
              <li key={board.id} className="tab tab-active">
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
              </li>
            );
          }
          return (
            <li key={board.id} className={active ? "tab tab-active" : "tab"}>
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
            </li>
          );
        })}
      </ul>
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
