"use client";

import { AgentAvatar } from "@/components/AgentAvatar";
import type { AgentIdentity } from "@/lib/agents-api";
import {
  DEFAULT_AGENT_NAME,
  messageAgentName,
  switchLabel,
  type AgentSwitchEvent,
  type AttributedMessage,
} from "@/lib/agent-attribution";
import { cn } from "@/lib/utils";
import ClientTimestamp from "./ClientTimestamp";

function shortRole(description?: string): string {
  if (!description) return "";
  const first = description.split(/[.\n—–-]/)[0].trim();
  return first.length > 40 ? `${first.slice(0, 38)}…` : first;
}

/** "[avatar] Harry · Account receivables v3" on an assistant reply. */
export function AgentAuthor({
  message,
  agents,
  className,
}: {
  message: AttributedMessage;
  agents: Record<string, AgentIdentity>;
  className?: string;
}) {
  const agent = message.agent_id ? agents[message.agent_id] : undefined;
  const name = messageAgentName(message);
  const role = message.agent_id ? shortRole(agent?.description) : "";
  const title = message.attribution === "inferred"
    ? "Saved before replies recorded their agent, so it is shown as Loma."
    : message.agent_id
      ? `Answered by ${name}${message.agent_config_version ? `, agent config v${message.agent_config_version}` : ""}`
      : `Answered by ${DEFAULT_AGENT_NAME}, the default agent`;
  return (
    <span className={cn("inline-flex items-center gap-1.5 min-w-0", className)} title={title}>
      {agent ? (
        <AgentAvatar avatar={agent.avatar} size={16} className="rounded-full flex-shrink-0" />
      ) : (
        <span
          aria-hidden
          className="inline-flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full bg-brand-100 text-[9px] font-semibold text-brand-700"
        >
          {name.charAt(0).toUpperCase()}
        </span>
      )}
      <span className="text-[11px] font-semibold text-foreground/80 truncate">{name}</span>
      {role && <span className="text-[11px] text-muted-foreground truncate">· {role}</span>}
      {message.agent_id && message.agent_config_version ? (
        <span className="text-[10px] text-muted-foreground/70 font-mono">v{message.agent_config_version}</span>
      ) : null}
    </span>
  );
}

/** A thin divider where the thread changed agents. */
export function AgentSwitchDivider({ event }: { event: AgentSwitchEvent }) {
  return (
    <div className="flex items-center gap-2 py-1 text-[11px] text-muted-foreground" role="separator">
      <span className="h-px flex-1 bg-border" />
      <span className="whitespace-nowrap">
        {switchLabel(event)}
        {event.source === "slack" ? " in Slack" : ""}
        {" · "}
        <ClientTimestamp iso={event.timestamp} variant="time" className="text-[11px]" />
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
