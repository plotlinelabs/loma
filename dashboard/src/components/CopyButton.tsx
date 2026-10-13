"use client";

import { useState } from "react";
import { RiCheckLine, RiFileCopyLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Icon button that copies `text` and briefly shows a check mark. */
export function CopyButton({ text, getText, label = "Copy", className }: {
  text?: string;
  /** Read the text at click time (e.g. from a rendered element). */
  getText?: () => string;
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn("h-7 w-7 p-0 shrink-0", className)}
      aria-label={copied ? "Copied" : label}
      title={copied ? "Copied" : label}
      onClick={async (e) => {
        // Cards and rows that host this button are clickable themselves.
        e.stopPropagation();
        try {
          await navigator.clipboard.writeText(getText ? getText() : text ?? "");
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          // Clipboard needs a secure context; the text is still selectable.
        }
      }}
    >
      {copied ? <RiCheckLine size={14} className="text-green-500" /> : <RiFileCopyLine size={14} />}
    </Button>
  );
}
