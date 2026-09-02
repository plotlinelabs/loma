"use client";

import { useMemo, useState } from "react";
import { RiArrowDownSLine, RiEqualizerLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import type { AvailableTool } from "@/lib/api";
import type { ToolsLoadState } from "@/hooks/useToolsPicker";

interface ToolsPickerProps {
  tools: AvailableTool[];
  isToolEnabled: (id: string) => boolean;
  onToggleTool: (id: string) => void;
  onEnableAll: () => void;
  onOpen: () => void;
  isAllEnabled: boolean;
  disabledCount: number;
  loadState: ToolsLoadState;
  disabled?: boolean;
  isAlwaysEnabled: (toolId: string) => boolean;
  hasAgentScope?: boolean;
}

export function ToolsPicker({
  tools,
  isToolEnabled,
  onToggleTool,
  onEnableAll,
  onOpen,
  isAllEnabled,
  disabledCount,
  loadState,
  disabled,
  isAlwaysEnabled,
  hasAgentScope,
}: ToolsPickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen) {
      onOpen();
      setSearch("");
    }
  };

  const builtinTools = useMemo(() => {
    const query = search.trim().toLowerCase();
    const items = tools.filter((t) => t.group === "built-in");
    if (!query) return items;
    return items.filter((t) =>
      t.name.toLowerCase().includes(query) ||
      (t.description || "").toLowerCase().includes(query),
    );
  }, [tools, search]);

  const integrationTools = useMemo(() => {
    const query = search.trim().toLowerCase();
    const items = tools.filter((t) => t.group === "integrations");
    if (!query) return items;
    return items.filter((t) =>
      t.name.toLowerCase().includes(query) ||
      (t.description || "").toLowerCase().includes(query),
    );
  }, [tools, search]);

  const isLoading = loadState === "loading" || loadState === "idle";

  const label = isAllEnabled
    ? "All tools"
    : disabledCount === 1
      ? "1 off"
      : `${disabledCount} off`;

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          title="Configure available tools"
          className="group inline-flex h-7 max-w-full items-center gap-1 rounded-md px-1.5 text-left text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-55"
        >
          <RiEqualizerLine size={12} className="shrink-0" />
          <span className={cn(
            "min-w-0 truncate text-xs",
            isAllEnabled ? "text-muted-foreground" : "text-amber-600 dark:text-amber-400",
          )}>
            {label}
          </span>
          <RiArrowDownSLine
            size={12}
            className={cn(
              "shrink-0 text-gray-400 transition-transform",
              open && "rotate-180",
            )}
          />
        </button>
      </PopoverTrigger>

      <PopoverContent
        side="top"
        align="start"
        className="w-[min(85vw,300px)] p-0 overflow-hidden rounded-xl"
      >
        <Command shouldFilter={false}>
          <div className="border-b border-border p-2.5">
            <button
              type="button"
              onClick={onEnableAll}
              className={cn(
                "flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-[13px] transition-colors",
                isAllEnabled
                  ? "bg-muted/60 text-foreground"
                  : "bg-muted/30 text-muted-foreground hover:bg-muted/60",
              )}
            >
              <span className="font-medium">
                {hasAgentScope ? "All agent tools" : "All tools"}
              </span>
              <Switch
                size="sm"
                checked={isAllEnabled}
                onCheckedChange={() => { if (!isAllEnabled) onEnableAll(); }}
              />
            </button>
          </div>

          <div className="border-b border-border p-1.5">
            <CommandInput
              value={search}
              onValueChange={setSearch}
              placeholder="Search tools"
            />
          </div>

          <CommandList>
            <ScrollArea className="max-h-64">
              {isLoading ? (
                <div className="px-3 py-6 text-center text-[13px] text-muted-foreground">
                  Loading...
                </div>
              ) : (
                <>
                  <CommandEmpty className="px-3 py-6 text-center text-[13px] text-muted-foreground">
                    No tools match.
                  </CommandEmpty>

                  {builtinTools.length > 0 && (
                    <CommandGroup heading="Built-in">
                      {builtinTools.map((tool) => {
                        const enabled = isToolEnabled(tool.id);
                        const locked = isAlwaysEnabled(tool.id);
                        return (
                          <CommandItem
                            key={tool.id}
                            value={tool.id}
                            onSelect={() => !locked && onToggleTool(tool.id)}
                            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px]"
                          >
                            <div className="min-w-0 flex-1">
                              <span className={cn(
                                "truncate",
                                enabled ? "text-foreground" : "text-muted-foreground line-through",
                              )}>
                                {tool.name}
                              </span>
                              {tool.description && (
                                <span className="ml-1.5 text-[11px] text-muted-foreground/70">
                                  {tool.description}
                                </span>
                              )}
                            </div>
                            <Switch
                              size="sm"
                              checked={enabled}
                              disabled={locked}
                              onCheckedChange={() => !locked && onToggleTool(tool.id)}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </CommandItem>
                        );
                      })}
                    </CommandGroup>
                  )}

                  {integrationTools.length > 0 && (
                    <CommandGroup heading="Integrations">
                      {integrationTools.map((tool) => {
                        const enabled = isToolEnabled(tool.id);
                        return (
                          <CommandItem
                            key={tool.id}
                            value={tool.id}
                            onSelect={() => onToggleTool(tool.id)}
                            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px]"
                          >
                            <div className="min-w-0 flex-1">
                              <span className={cn(
                                "truncate",
                                enabled ? "text-foreground" : "text-muted-foreground line-through",
                              )}>
                                {tool.name}
                              </span>
                            </div>
                            <Switch
                              size="sm"
                              checked={enabled}
                              onCheckedChange={() => onToggleTool(tool.id)}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </CommandItem>
                        );
                      })}
                    </CommandGroup>
                  )}
                </>
              )}
            </ScrollArea>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
