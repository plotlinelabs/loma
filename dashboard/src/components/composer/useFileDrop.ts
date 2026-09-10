"use client";

import { useCallback, useState, type DragEvent } from "react";

export interface FileDropHandlers {
  onDragOver: (e: DragEvent) => void;
  onDragLeave: (e: DragEvent) => void;
  onDrop: (e: DragEvent) => void;
}

/**
 * Shared drag-and-drop wiring for composer surfaces (chat + tasks quick-add).
 *
 * Returns `isDragOver` (for rendering a drop overlay) and the drag handlers to
 * spread onto the drop target. Files of any type are forwarded to `onFiles`;
 * the composer's own handler decides how to convert/attach them. Centralising
 * this keeps drop behaviour identical everywhere a composer is used.
 */
export function useFileDrop(onFiles: (files: FileList | File[]) => void): {
  isDragOver: boolean;
  dropHandlers: FileDropHandlers;
} {
  const [isDragOver, setIsDragOver] = useState(false);

  const isFileDrag = (e: DragEvent) => {
    const types = e.dataTransfer?.types;
    // Some browsers report an empty list mid-drag — treat that as "maybe files".
    return !types || types.length === 0 || Array.from(types).includes("Files");
  };

  const onDragOver = useCallback((e: DragEvent) => {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    setIsDragOver(true);
  }, []);

  const onDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  }, []);

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      setIsDragOver(false);
      if (e.dataTransfer?.files?.length) onFiles(e.dataTransfer.files);
    },
    [onFiles],
  );

  return { isDragOver, dropHandlers: { onDragOver, onDragLeave, onDrop } };
}
