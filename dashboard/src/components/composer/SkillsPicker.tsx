"use client";

import { useMemo, useState } from "react";
import { RiArrowDownSLine, RiBookOpenLine } from "@remixicon/react";
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
import type { AvailableSkill } from "@/lib/api";
import type { ToolsLoadState } from "@/hooks/useToolsPicker";

interface SkillsPickerProps {
  skills: AvailableSkill[];
  isSkillEnabled: (slug: string) => boolean;
  onToggleSkill: (slug: string) => void;
  onEnableAll: () => void;
  onOpen: () => void;
  isAllEnabled: boolean;
  disabledCount: number;
  loadState: ToolsLoadState;
  disabled?: boolean;
  hasAgentScope?: boolean;
}

export function SkillsPicker({
  skills,
  isSkillEnabled,
  onToggleSkill,
  onEnableAll,
  onOpen,
  isAllEnabled,
  disabledCount,
  loadState,
  disabled,
  hasAgentScope,
}: SkillsPickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  if (skills.length === 0 && loadState !== "loading" && loadState !== "idle") {
    return null;
  }

  const handleOpenChange = (nextOpen: boolean) => {
    setOpen(nextOpen);
    if (nextOpen) {
      onOpen();
      setSearch("");
    }
  };

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return skills;
    return skills.filter((s) =>
      s.name.toLowerCase().includes(query) ||
      s.slug.toLowerCase().includes(query) ||
      s.description.toLowerCase().includes(query) ||
      (s.tags || []).some((tag) => tag.toLowerCase().includes(query)),
    );
  }, [skills, search]);

  const isLoading = loadState === "loading" || loadState === "idle";

  const label = isAllEnabled
    ? "All skills"
    : disabledCount === 1
      ? "1 off"
      : `${disabledCount} off`;

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          title="Configure available skills"
          className="group inline-flex h-7 max-w-full items-center gap-1 rounded-md px-1.5 text-left text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-55"
        >
          <RiBookOpenLine size={12} className="shrink-0" />
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
                {hasAgentScope ? "All agent skills" : "All skills"}
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
              placeholder="Search skills"
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
                    No skills match.
                  </CommandEmpty>

                  <CommandGroup heading="Skills">
                    {filtered.map((skill) => {
                      const enabled = isSkillEnabled(skill.slug);
                      return (
                        <CommandItem
                          key={skill.slug}
                          value={skill.slug}
                          onSelect={() => onToggleSkill(skill.slug)}
                          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px]"
                        >
                          <div className="min-w-0 flex-1">
                            <div className={cn(
                              "truncate",
                              enabled ? "text-foreground" : "text-muted-foreground line-through",
                            )}>
                              {skill.name}
                            </div>
                            {skill.description && (
                              <div className="truncate text-[11px] text-muted-foreground/70">
                                {skill.description}
                              </div>
                            )}
                          </div>
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
                </>
              )}
            </ScrollArea>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
