"use client";

import { RiListCheck3, RiSlashCommands2 } from "@remixicon/react";
import { cn } from "@/lib/utils";
import type { PromptTemplate } from "@/lib/api";

/** Commands matching what's typed so far (`/rev` → review). Empty when not typing a command name. */
export function slashMatches(templates: PromptTemplate[], input: string): PromptTemplate[] {
  if (!/^\/[a-z0-9-]*$/.test(input)) return [];
  const query = input.slice(1);
  return templates.filter((t) => t.name.startsWith(query)).slice(0, 8);
}

/** Pop-up list of slash commands above the composer. */
export function SlashCommandMenu({
  matches,
  activeIndex,
  onSelect,
  onHover,
}: {
  matches: PromptTemplate[];
  activeIndex: number;
  onSelect: (template: PromptTemplate) => void;
  onHover: (index: number) => void;
}) {
  if (!matches.length) return null;
  return (
    <div
      role="listbox"
      aria-label="Commands"
      className="absolute inset-x-0 bottom-full z-30 mb-2 overflow-hidden rounded-xl border border-border bg-popover p-1 shadow-lg"
    >
      {matches.map((t, i) => (
        <button
          key={t.name}
          type="button"
          role="option"
          aria-selected={i === activeIndex}
          // Keep focus in the composer so typing continues uninterrupted.
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => onSelect(t)}
          onMouseEnter={() => onHover(i)}
          className={cn(
            "flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px]",
            i === activeIndex ? "bg-muted text-foreground" : "text-foreground/80",
          )}
        >
          <span className="font-mono text-[12px] font-medium">/{t.name}</span>
          {t.input_hint && <span className="font-mono text-[11px] text-muted-foreground">{t.input_hint}</span>}
          <span className="ml-auto truncate pl-3 text-xs text-muted-foreground">{t.description}</span>
          {t.plan_mode && <RiListCheck3 size={13} className="shrink-0 text-muted-foreground" aria-label="Turns on plan mode" />}
          {!t.builtin && <span className="shrink-0 rounded bg-muted px-1 text-[10px] text-muted-foreground">yours</span>}
        </button>
      ))}
    </div>
  );
}

/** One-line hint above the composer once a command is typed. */
export function SlashCommandHint({ template }: { template: PromptTemplate }) {
  return (
    <div className="mb-1.5 flex items-center gap-1.5 px-1 text-[11px] text-muted-foreground">
      <RiSlashCommands2 size={12} />
      <span className="font-mono text-foreground/80">/{template.name}</span>
      {template.input_hint && <span className="font-mono">{template.input_hint}</span>}
      <span aria-hidden>·</span>
      <span className="truncate">{template.description}</span>
      {template.plan_mode && <span className="shrink-0">· turns on plan mode</span>}
    </div>
  );
}
