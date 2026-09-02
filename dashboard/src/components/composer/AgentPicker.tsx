"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { RiArrowDownSLine, RiCheckLine, RiAddLine } from "@remixicon/react";
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
import { AgentAvatar } from "@/components/AgentAvatar";
import type { AgentIdentity } from "@/lib/agents-api";
import type { AgentLoadState } from "@/hooks/useAgentIdentities";

interface AgentPickerProps {
  agents: AgentIdentity[];
  selectedAgentId: string | null;
  onSelect: (agentId: string | null) => void;
  loadState: AgentLoadState;
  disabled?: boolean;
}

/** Compact agent picker used inside composers, next to the model picker.
 *  `null` selection is the default Loma agent. */
export function AgentPicker({ agents, selectedAgentId, onSelect, loadState, disabled }: AgentPickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  const selectedAgent = useMemo(
    () => agents.find((a) => a.agent_id === selectedAgentId) || null,
    [agents, selectedAgentId],
  );

  const filtered = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return agents;
    return agents.filter((a) =>
      `${a.name} ${a.description}`.toLowerCase().includes(query),
    );
  }, [agents, search]);

  // Hide the picker entirely until the workspace has at least one agent —
  // the default Loma agent needs no chrome.
  if (loadState !== "ready" || agents.length === 0) return null;

  const handleSelect = (agentId: string | null) => {
    onSelect(agentId);
    setOpen(false);
    setSearch("");
  };

  const renderItem = (agent: AgentIdentity) => {
    const isSelected = agent.agent_id === selectedAgentId;
    return (
      <CommandItem
        key={agent.agent_id}
        value={agent.agent_id}
        onSelect={() => handleSelect(agent.agent_id)}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px]",
          isSelected ? "text-foreground font-medium" : "text-muted-foreground",
        )}
      >
        <AgentAvatar avatar={agent.avatar} size={20} />
        <span className="min-w-0 flex-1">
          <span className="block truncate">{agent.name}</span>
          <span className="block truncate text-[11px] font-normal text-muted-foreground">
            {agent.description}
          </span>
        </span>
        {isSelected && <RiCheckLine size={14} className="shrink-0 text-foreground" />}
      </CommandItem>
    );
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          title="Choose agent"
          className="group inline-flex h-7 max-w-full items-center gap-1.5 rounded-md px-1.5 text-left text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-55"
        >
          {selectedAgent ? (
            <AgentAvatar avatar={selectedAgent.avatar} size={16} />
          ) : (
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />
          )}
          <span className="min-w-0 truncate text-xs text-muted-foreground">
            {selectedAgent ? selectedAgent.name : "Loma"}
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
        className="w-[min(80vw,300px)] p-0 overflow-hidden rounded-xl"
      >
        <Command shouldFilter={false}>
          <div className="border-b border-border p-1.5">
            <CommandInput
              value={search}
              onValueChange={setSearch}
              placeholder="Search agents"
            />
          </div>
          <CommandList>
            <ScrollArea className="max-h-64">
              <CommandEmpty className="px-3 py-6 text-center text-[13px] text-muted-foreground">
                No agents match that search.
              </CommandEmpty>
              {!search && (
                <CommandGroup>
                  <CommandItem
                    value="__default__"
                    onSelect={() => handleSelect(null)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-[13px]",
                      !selectedAgentId ? "text-foreground font-medium" : "text-muted-foreground",
                    )}
                  >
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-accent-200 text-[10px] font-semibold text-accent-on">
                      L
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">Loma</span>
                      <span className="block truncate text-[11px] font-normal text-muted-foreground">
                        The default agent — full workspace scope
                      </span>
                    </span>
                    {!selectedAgentId && <RiCheckLine size={14} className="shrink-0 text-foreground" />}
                  </CommandItem>
                </CommandGroup>
              )}
              <CommandGroup>{filtered.map(renderItem)}</CommandGroup>
            </ScrollArea>
          </CommandList>
          <div className="border-t border-border p-1.5">
            <Link
              href="/agents"
              onClick={() => setOpen(false)}
              className="flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <RiAddLine size={14} />
              Manage agents
            </Link>
          </div>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
