"use client";

import { useState } from "react";
import Link from "next/link";
import { RiArrowDownSLine, RiLockLine } from "@remixicon/react";
import { cn } from "@/lib/utils";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/useIsMobile";
import type { AgentIdentity } from "@/lib/agents-api";
import type { AvailableSkill } from "@/lib/api";
import {
  agentScopeSkills,
  agentScopeSummary,
  agentScopeTools,
  type AgentScopeItem,
} from "@/hooks/agent-scope";
import { PERSONAL_TOOL_LABELS } from "@/app/agents/selection-options";
import { useUser } from "@/lib/UserContext";

interface AgentScopeProps {
  agent: AgentIdentity;
  /** Skill catalog from the tools picker; resolves slugs to names once loaded. */
  skills: AvailableSkill[];
  onOpen: () => void;
  onUseDefault: () => void;
  disabled?: boolean;
}

function Section({ title, items }: { title: string; items: AgentScopeItem[] }) {
  return (
    <section className="px-3 py-2.5">
      <h3 className="mb-1 flex items-baseline justify-between text-xs font-semibold text-foreground">
        {title}
        <span className="font-normal text-muted-foreground">
          {items.length || "All"}
        </span>
      </h3>
      {items.length ? (
        <ul className="space-y-1">
          {items.map((item) => (
            <li key={item.id} className="text-[13px] text-foreground">
              <span className="block break-words">{item.name}</span>
              {item.description && (
                <span className="block truncate text-[11px] text-muted-foreground">
                  {item.description}
                </span>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-muted-foreground">
          No limit set. This agent can use all available {title.toLowerCase()}.
        </p>
      )}
    </section>
  );
}

/** Read-only replacement for the Tools/Skills pickers while an agent is
 *  selected: the agent's scope applies, so there is nothing to pick. */
export function AgentScope({ agent, skills, onOpen, onUseDefault, disabled }: AgentScopeProps) {
  const [open, setOpen] = useState(false);
  const isMobile = useIsMobile();
  const { user } = useUser();
  // Same rule as the Agents page: only the creator or an admin can edit.
  const canEdit = user?.email === agent.created_by || user?.system_role === "admin";
  const summary = agentScopeSummary(agent);
  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (next) onOpen();
  };

  const trigger = (
    <button
      type="button"
      disabled={disabled}
      aria-label={`${agent.name} scope: ${summary}. Set by the agent.`}
      title={`Set by ${agent.name}`}
      className="touch-target inline-flex h-8 max-w-full items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-55"
    >
      <RiLockLine size={13} className="shrink-0" aria-hidden="true" />
      <span className="truncate">{summary}</span>
      <RiArrowDownSLine
        size={14}
        className={cn("shrink-0 transition-transform", open && "rotate-180")}
      />
    </button>
  );

  const heading = `${agent.name}'s tools and skills`;
  const content = (
    <>
      <div className="border-b border-border p-3">
        {isMobile ? (
          <SheetTitle>{heading}</SheetTitle>
        ) : (
          <div className="text-sm font-semibold">{heading}</div>
        )}
        <p className="mt-1 text-xs text-muted-foreground">
          Set by the agent, so they can&apos;t be changed here. Your own Tools
          and Skills choices come back when you switch to Loma.
        </p>
      </div>
      <div className="min-h-0 flex-1 divide-y divide-border overflow-y-auto overscroll-contain">
        <Section title="Tools" items={agentScopeTools(agent, PERSONAL_TOOL_LABELS)} />
        <Section title="Skills" items={agentScopeSkills(agent, skills)} />
      </div>
      <div className="flex items-center justify-between gap-2 border-t border-border p-1.5 text-xs">
        {canEdit ? (
          <Link
            href={`/agents?edit=${encodeURIComponent(agent.agent_id)}`}
            onClick={() => setOpen(false)}
            className="rounded-md px-2 py-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            Edit agent
          </Link>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={() => {
            setOpen(false);
            onUseDefault();
          }}
          className="rounded-md px-2 py-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          Switch to Loma to choose your own
        </button>
      </div>
    </>
  );

  if (isMobile) {
    return (
      <Sheet open={open} onOpenChange={changeOpen}>
        <SheetTrigger asChild>{trigger}</SheetTrigger>
        <SheetContent
          side="bottom"
          aria-describedby={undefined}
          className="max-h-[85dvh] gap-0 overflow-hidden rounded-t-2xl p-0 pb-[env(safe-area-inset-bottom)]"
        >
          {content}
        </SheetContent>
      </Sheet>
    );
  }

  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent
        side="top"
        align="start"
        aria-label={heading}
        className="flex w-[min(90vw,340px)] max-h-[min(480px,var(--radix-popover-content-available-height))] flex-col gap-0 overflow-hidden rounded-xl p-0"
      >
        {content}
      </PopoverContent>
    </Popover>
  );
}
