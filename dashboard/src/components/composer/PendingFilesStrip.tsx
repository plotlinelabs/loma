"use client";

import { RiAttachmentLine, RiCloseLine } from "@remixicon/react";
import { Badge } from "@/components/ui/badge";
import type { ChatFile } from "@/lib/api";

function PendingImageThumbnail({
  file,
  index,
  onRemove,
  onExpand,
}: {
  file: ChatFile;
  index: number;
  onRemove: (i: number) => void;
  onExpand?: (src: string) => void;
}) {
  const src = `data:${file.mimetype};base64,${file.data}`;
  return (
    <span className="relative inline-flex group">
      <button
        type="button"
        onClick={() => onExpand?.(src)}
        className="w-12 h-12 rounded-lg overflow-hidden border border-gray-200 hover:border-gray-300 transition-colors flex-shrink-0"
      >
        <img src={src} alt={file.name} className="w-full h-full object-cover" />
      </button>
      <button
        type="button"
        onClick={() => onRemove(index)}
        className="absolute -top-1.5 -right-1.5 w-4 h-4 bg-gray-600 hover:bg-gray-800 text-white rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
      >
        <RiCloseLine size={10} />
      </button>
    </span>
  );
}

function PendingFileBadge({
  file,
  index,
  onRemove,
}: {
  file: ChatFile;
  index: number;
  onRemove: (i: number) => void;
}) {
  return (
    <Badge variant="secondary" className="h-auto gap-1.5 px-2.5 py-1 rounded-lg border border-gray-200">
      <RiAttachmentLine size={12} className="text-muted-foreground" />
      {file.name}
      <button
        type="button"
        onClick={() => onRemove(index)}
        className="text-muted-foreground hover:text-foreground ml-0.5"
      >
        <RiCloseLine size={12} />
      </button>
    </Badge>
  );
}

/** Attachment chips shown above a composer (chat + tasks quick-add). */
export function PendingFilesStrip({
  files,
  onRemove,
  onExpandImage,
}: {
  files: ChatFile[];
  onRemove: (i: number) => void;
  onExpandImage?: (src: string) => void;
}) {
  if (files.length === 0) return null;
  return (
    <div className="flex flex-wrap items-end gap-1.5 mb-2">
      {files.map((f, i) =>
        f.type === "image" ? (
          <PendingImageThumbnail key={i} file={f} index={i} onRemove={onRemove} onExpand={onExpandImage} />
        ) : (
          <PendingFileBadge key={i} file={f} index={i} onRemove={onRemove} />
        )
      )}
    </div>
  );
}
