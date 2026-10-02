"use client";

import { useState } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";

const EMOJIS = [
  "🏠", "🚀", "📣", "🧭", "🛠️", "📊", "🎯", "💡", "🧪", "📦", "🌱", "🔥",
  "🗂️", "🧑‍💼", "🤝", "💰", "📈", "🐛", "🔒", "🎨", "✍️", "📚", "🎉", "⭐",
  "🧠", "📅", "📞", "🌍", "⚙️", "🧾", "🏆", "🐶", "☕", "🎧", "🧩", "❤️",
];

/** Small emoji grid in a popover. Free text works too, for any other emoji. */
export function EmojiPicker({
  value,
  onPick,
  children,
  open,
  onOpenChange,
}: {
  value?: string;
  onPick: (emoji: string) => void;
  children: React.ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [custom, setCustom] = useState("");
  const pick = (emoji: string) => {
    onOpenChange(false);
    setCustom("");
    if (emoji && emoji !== value) onPick(emoji);
  };
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent side="right" align="start" className="w-[248px] p-2">
        <div className="grid grid-cols-8 gap-0.5" role="listbox" aria-label="Board emoji">
          {EMOJIS.map((emoji) => (
            <button
              key={emoji}
              type="button"
              role="option"
              aria-selected={emoji === value}
              onClick={() => pick(emoji)}
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-md text-[16px] outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring",
                emoji === value && "bg-muted",
              )}
            >
              {emoji}
            </button>
          ))}
        </div>
        <form
          className="mt-2 flex gap-1"
          onSubmit={(e) => { e.preventDefault(); pick(custom.trim()); }}
        >
          <input
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            placeholder="Or type / paste one"
            aria-label="Custom emoji"
            className="h-7 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-[12px] outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button type="submit" className="h-7 rounded-md bg-muted px-2 text-[12px] font-medium hover:bg-muted/70">
            Use
          </button>
        </form>
      </PopoverContent>
    </Popover>
  );
}
