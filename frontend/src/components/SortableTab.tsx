import type { ReactNode } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

export default function SortableTab({
  id,
  label,
  active,
  disabled,
  children,
}: {
  id: string;
  label: string;
  active: boolean;
  disabled: boolean;
  children: ReactNode;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id, disabled });

  return (
    <li
      ref={setNodeRef}
      className={`tab${active ? " tab-active" : ""}${isDragging ? " tab-dragging" : ""}`}
      style={{ transform: CSS.Transform.toString(transform), transition }}
    >
      <button
        type="button"
        className="tab-drag"
        {...attributes}
        {...listeners}
        aria-label={`Reorder ${label}`}
        disabled={disabled}
      >
        <span aria-hidden="true" />
      </button>
      {children}
    </li>
  );
}
