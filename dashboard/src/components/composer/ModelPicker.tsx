"use client";

import { useMemo, useState } from "react";
import { RiArrowDownSLine, RiCheckLine } from "@remixicon/react";
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
import type { AgentModel } from "@/lib/api";
import { favoriteModelRank, isFavoriteModel, type ModelLoadState } from "@/hooks/useAgentModels";

interface ModelPickerProps {
  models: AgentModel[];
  selectedModel: string;
  onSelect: (id: string) => void;
  loadState: ModelLoadState;
  disabled?: boolean;
}

/** Compact model picker used inside composers (chat + tasks quick-add). */
export function ModelPicker({ models, selectedModel, onSelect, loadState, disabled }: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  const selectedModelInfo = useMemo(
    () => models.find((model) => model.id === selectedModel) || null,
    [models, selectedModel],
  );

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return models;
    return models.filter((model) => {
      const haystack = `${model.label} ${model.id} ${model.provider_id} ${model.model_id}`.toLowerCase();
      return haystack.includes(query);
    });
  }, [models, search]);

  const recommended = useMemo(
    () => filtered
      .filter(isFavoriteModel)
      .sort((a, b) => (favoriteModelRank(a) ?? 99) - (favoriteModelRank(b) ?? 99)),
    [filtered],
  );

  const grouped = useMemo(() => {
    const groups: Array<{ providerId: string; models: AgentModel[] }> = [];
    for (const model of filtered.filter((item) => !isFavoriteModel(item))) {
      const group = groups.find((item) => item.providerId === model.provider_id);
      if (group) {
        group.models.push(model);
      } else {
        groups.push({ providerId: model.provider_id, models: [model] });
      }
    }
    return groups;
  }, [filtered]);

  const isDisabled = disabled || loadState !== "ready" || models.length === 0;
  const title = loadState === "error"
    ? "Model list unavailable; backend default will be used"
    : "Choose model";
  const modelLabel = selectedModelInfo
    ? selectedModelInfo.label.split("·").pop()?.trim() || selectedModelInfo.model_id
    : loadState === "loading"
    ? "Loading models"
    : loadState === "error"
    ? "Default model"
    : "Choose model";

  const handleSelect = (id: string) => {
    onSelect(id);
    setOpen(false);
    setSearch("");
  };

  const renderItem = (model: AgentModel) => {
    const isSelected = model.id === selectedModel;
    const itemModelLabel = model.label.split("·").pop()?.trim() || model.model_id;
    return (
      <CommandItem
        key={model.id}
        value={model.id}
        onSelect={() => handleSelect(model.id)}
        data-checked={isSelected}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px]",
          isSelected ? "text-foreground font-medium" : "text-muted-foreground"
        )}
      >
        <span className="min-w-0 flex-1 truncate">{itemModelLabel}</span>
        {model.supports_reasoning && (
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent-500/60" title="reasoning" />
        )}
        {isSelected && <RiCheckLine size={14} className="shrink-0 text-foreground" />}
      </CommandItem>
    );
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={isDisabled}
          title={title}
          className="group inline-flex h-7 max-w-full items-center gap-1.5 rounded-md px-1.5 text-left text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-55"
        >
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500" />
          <span className="min-w-0 truncate text-xs text-muted-foreground">
            {modelLabel}
          </span>
          <RiArrowDownSLine
            size={14}
            className={cn(
              "shrink-0 text-gray-400 transition-transform",
              open && "rotate-180"
            )}
          />
        </button>
      </PopoverTrigger>

      <PopoverContent
        side="top"
        align="start"
        className="w-[min(80vw,280px)] p-0 overflow-hidden rounded-xl"
      >
        <Command shouldFilter={false}>
          <div className="border-b border-border p-1.5">
            <CommandInput
              value={search}
              onValueChange={setSearch}
              placeholder="Search models"
            />
          </div>
          <CommandList>
            <ScrollArea className="max-h-64">
              <CommandEmpty className="px-3 py-6 text-center text-[13px] text-muted-foreground">
                No models match that search.
              </CommandEmpty>

              {recommended.length > 0 && (
                <CommandGroup heading="Favorites">
                  {recommended.map(renderItem)}
                </CommandGroup>
              )}
              {grouped.map((group) => (
                <CommandGroup key={group.providerId} heading={group.providerId}>
                  {group.models.map(renderItem)}
                </CommandGroup>
              ))}
            </ScrollArea>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
