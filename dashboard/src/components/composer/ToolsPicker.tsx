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
import type { AvailableTool, AvailableSkill } from "@/lib/api";
import type { ToolsLoadState, ToolsSelection } from "@/hooks/useToolsPicker";

interface ToolsPickerProps {
  tools: AvailableTool[];
  skills: AvailableSkill[];
  selection: ToolsSelection;
  onToggleTool: (id: string) => void;
  onToggleSkill: (slug: string) => void;
  onEnableAll: () => void;
  onOpen: () => void;
  isAllEnabled: boolean;
  disabledCount: number;
  loadState: ToolsLoadState;
  disabled?: boolean;
  isAlwaysEnabled: (toolId: string) => boolean;
}

export function ToolsPicker({
  tools,
  skills,
  selection,
  onToggleTool,
  onToggleSkill,
  onEnableAll,
  onOpen,
  isAllEnabled,
  disabledCount,
  loadState,
  disabled,
  isAlwaysEnabled,
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

  const isToolEnabled = (id: string) =>
    selection.enabledTools === null || selection.enabledTools.includes(id);

  const isSkillEnabled = (slug: string) =>
    selection.enabledSkills === null || selection.enabledSkills.includes(slug);

  const filteredTools = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return tools;
    return tools.filter(
      (t) =>
        t.name.toLowerCase().includes(query) ||
        t.id.toLowerCase().includes(query) ||
        (t.description || "").toLowerCase().includes(query),
    );
  }, [tools, search]);

  const filteredSkills = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return skills;
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(query) ||
        s.slug.toLowerCase().includes(query) ||
        s.description.toLowerCase().includes(query) ||
        (s.tags || []).some((tag) => tag.toLowerCase().includes(query)),
    );
  }, [skills, search]);

  const isDisabled = disabled;
  const isLoading = loadState === "loading" || loadState === "idle";

  const label = isAllEnabled
    ? "All tools"
    : disabledCount === 1
      ? "1 disabled"
      : `${disabledCount} disabled`;

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={isDisabled}
          title="Configure available tools & skills"
          className="group inline-flex h-7 max-w-full items-center gap-1.5 rounded-md px-1.5 text-left text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-55"
        >
          <RiEqualizerLine size={13} className="shrink-0" />
          <span className={cn(
            "min-w-0 truncate text-xs",
            isAllEnabled ? "text-muted-foreground" : "text-amber-600 dark:text-amber-400",
          )}>
            {label}
          </span>
          <RiArrowDownSLine
            size={14}
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
        className="w-[min(85vw,320px)] p-0 overflow-hidden rounded-xl"
      >
        <Command shouldFilter={false}>
          {/* Enable all toggle */}
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
              <span className="font-medium">All tools & skills</span>
              <Switch
                size="sm"
                checked={isAllEnabled}
                onCheckedChange={() => {
                  if (!isAllEnabled) onEnableAll();
                }}
              />
            </button>
          </div>

          <div className="border-b border-border p-1.5">
            <CommandInput
              value={search}
              onValueChange={setSearch}
              placeholder="Search tools & skills"
            />
          </div>

          <CommandList>
            <ScrollArea className="max-h-72">
              {isLoading ? (
                <div className="px-3 py-6 text-center text-[13px] text-muted-foreground">
                  Loading tools...
                </div>
              ) : (
                <>
                  <CommandEmpty className="px-3 py-6 text-center text-[13px] text-muted-foreground">
                    No matches.
                  </CommandEmpty>

                  {filteredTools.length > 0 && (
                    <CommandGroup heading="Tools">
                      {filteredTools.map((tool) => {
                        const enabled = isToolEnabled(tool.id);
                        const locked = isAlwaysEnabled(tool.id);
                        return (
                          <CommandItem
                            key={tool.id}
                            value={tool.id}
                            onSelect={() => !locked && onToggleTool(tool.id)}
                            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px]"
                          >
                            <span className="min-w-0 flex-1">
                              <span className={cn(
                                "truncate",
                                enabled ? "text-foreground" : "text-muted-foreground line-through",
                              )}>
                                {tool.name}
                              </span>
                              {tool.group === "integrations" && (
                                <span className="ml-1.5 text-[11px] text-muted-foreground">
                                  integration
                                </span>
                              )}
                            </span>
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

                  {filteredSkills.length > 0 && (
                    <CommandGroup heading="Skills">
                      {filteredSkills.map((skill) => {
                        const enabled = isSkillEnabled(skill.slug);
                        return (
                          <CommandItem
                            key={skill.slug}
                            value={skill.slug}
                            onSelect={() => onToggleSkill(skill.slug)}
                            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px]"
                          >
                            <span className="min-w-0 flex-1">
                              <span className={cn(
                                "truncate",
                                enabled ? "text-foreground" : "text-muted-foreground line-through",
                              )}>
                                {skill.name}
                              </span>
                            </span>
                            <Switch
                              size="sm"
                              checked={enabled}
                              onCheckedChange={() => onToggleSkill(skill.slug)}
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
