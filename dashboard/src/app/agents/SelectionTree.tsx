"use client";

import { useId, useMemo, useState } from "react";
import { RiArrowRightSLine, RiFolderLine } from "@remixicon/react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { toggleSelection, withSavedOptions, type SelectionOption } from "./selection-options";

interface Group {
  label: string;
  path: string[];
  groups: Map<string, Group>;
  items: SelectionOption[];
  values: string[];
}

export function SelectionTree({ label, options, selected, onChange, error }: {
  label: string;
  options: SelectionOption[];
  selected: string[];
  onChange: (selected: string[]) => void;
  error?: string;
}) {
  const id = useId();
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(new Set([JSON.stringify(["Personal"]), JSON.stringify(["Organisation"]), JSON.stringify(["Saved selections"])]));
  const search = query.trim().toLowerCase();
  const groups = useMemo(() => {
    const root = new Map<string, Group>();
    if (!search) {
      for (const label of ["Personal", "Organisation"]) {
        root.set(label, { label, path: [label], groups: new Map(), items: [], values: [] });
      }
    }
    for (const option of withSavedOptions(options, selected)) {
      if (search && ![option.label, option.value, option.description, ...option.path].join(" ").toLowerCase().includes(search)) continue;
      let siblings = root;
      option.path.forEach((label, index) => {
        if (!siblings.has(label)) siblings.set(label, { label, path: option.path.slice(0, index + 1), groups: new Map(), items: [], values: [] });
        const group = siblings.get(label)!;
        group.values.push(option.value);
        if (index === option.path.length - 1) group.items.push(option);
        siblings = group.groups;
      });
    }
    return root;
  }, [options, selected, search]);

  function renderGroup(group: Group, depth: number) {
    const key = JSON.stringify(group.path);
    const open = !!search || expanded.has(key);
    const count = group.values.filter((value) => selected.includes(value)).length;
    return (
      <div key={key}>
        <button
          type="button"
          aria-expanded={open}
          aria-label={group.path.join(" / ")}
          onClick={() => setExpanded((previous) => {
            const next = new Set(previous);
            if (next.has(key)) next.delete(key); else next.add(key);
            return next;
          })}
          className={cn("flex w-full items-center gap-1.5 rounded-md px-2 py-2 text-left text-xs hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring", depth === 0 ? "font-semibold" : "text-muted-foreground")}
        >
          <RiArrowRightSLine size={14} className={cn("shrink-0 transition-transform", open && "rotate-90")} />
          {depth > 0 && <RiFolderLine size={14} className="shrink-0 text-amber-500" />}
          <span className="min-w-0 flex-1 truncate">{group.label}</span>
          <span className="shrink-0 text-[11px] font-normal text-muted-foreground">{count}/{group.values.length}</span>
        </button>
        {open && (
          <div className="ml-3 border-l border-border pl-2">
            {[...group.groups.values()].sort((a, b) => a.label.localeCompare(b.label)).map((child) => renderGroup(child, depth + 1))}
            {group.items.sort((a, b) => a.label.localeCompare(b.label)).map((option) => (
              <label key={option.value} title={option.description} className="flex cursor-pointer items-start gap-2 rounded-md px-2 py-1.5 text-[13px] hover:bg-muted">
                <input type="checkbox" checked={selected.includes(option.value)} onChange={() => onChange(toggleSelection(selected, option.value))} className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-brand-600)]" />
                <span className="min-w-0 break-words">{option.label}</span>
              </label>
            ))}
            {group.values.length === 0 && <p className="px-2 py-1.5 text-xs text-muted-foreground">No {label.toLowerCase()} available</p>}
          </div>
        )}
      </div>
    );
  }

  return (
    <section aria-labelledby={`${id}-label`} className="grid gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <h3 id={`${id}-label`} className="text-sm font-medium">{label}</h3>
        <span className="text-xs text-muted-foreground">{selected.length ? `${selected.length} selected` : "All (no restriction)"}</span>
      </div>
      <p id={`${id}-help`} className="text-xs text-muted-foreground">Check items to focus the agent. No checkmarks means all {label.toLowerCase()}.</p>
      {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
      <div className="rounded-lg border border-border bg-card p-2">
        <Input aria-label={`Search ${label.toLowerCase()}`} aria-describedby={`${id}-help`} placeholder={`Search ${label.toLowerCase()} or folders...`} value={query} onChange={(event) => setQuery(event.target.value)} className="mb-2 h-8 text-xs" />
        <div className="max-h-56 overflow-y-auto">
          {[...groups.values()].map((group) => renderGroup(group, 0))}
          {groups.size === 0 && <p className="p-2 text-xs text-muted-foreground">No matching {label.toLowerCase()}</p>}
        </div>
      </div>
    </section>
  );
}
