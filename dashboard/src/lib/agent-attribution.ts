/**
 * Which agent wrote what in a run. Pure helpers shared by the run view, the
 * runs list and the chat panel (unit tested in tests/agent-attribution.test.cjs).
 *
 * Replies saved before attribution existed have no agent: they are Loma's.
 */

export const DEFAULT_AGENT_NAME = "Loma";

export interface AttributedMessage {
  role: "user" | "assistant";
  content: string;
  timestamp?: string;
  sender?: string;
  agent_id?: string | null;
  agent_name?: string;
  agent_config_version?: number;
  attribution?: "inferred";
}

export interface AgentSwitchEvent {
  type: "agent_switch";
  from_agent_id: string | null;
  from_agent_name: string;
  to_agent_id: string | null;
  to_agent_name: string;
  to_agent_config_version?: number;
  switched_by?: string | null;
  source?: string;
  timestamp: string;
}

export interface BlockedCall {
  turn_number: number;
  tool_name: string;
  tool_use_id?: string | null;
  kind: "tool" | "skill";
  target: string;
  reason: string;
  timestamp: string;
  agent_id?: string | null;
  agent_name?: string;
}

export type TimelineItem<M extends AttributedMessage = AttributedMessage> =
  | { kind: "message"; message: M }
  | { kind: "switch"; event: AgentSwitchEvent };

export function versionLabel(version?: number | null): string {
  return version ? ` (v${version})` : "";
}

/** "Harry", or "Loma" for the default agent and old unattributed replies. */
export function messageAgentName(message: Pick<AttributedMessage, "agent_name">): string {
  return message.agent_name || DEFAULT_AGENT_NAME;
}

/** The run's current agent, e.g. "Harry (v3)" or "Loma". */
export function runAgentLabel(metadata?: Record<string, unknown> | null): string {
  const name = typeof metadata?.agent_name === "string" && metadata.agent_name
    ? metadata.agent_name
    : metadata?.agent_id ? "Agent" : DEFAULT_AGENT_NAME;
  const version = Number(metadata?.agent_config_version) || undefined;
  return `${name}${metadata?.agent_id ? versionLabel(version) : ""}`;
}

/** "sam@example.com" -> "sam@". Keeps the divider short. */
export function shortUser(user?: string | null): string {
  if (!user) return "someone";
  const at = user.indexOf("@");
  return at > 0 ? user.slice(0, at + 1) : user;
}

export function switchLabel(event: AgentSwitchEvent): string {
  return `Switched to ${event.to_agent_name || DEFAULT_AGENT_NAME} by ${shortUser(event.switched_by)}`;
}

/** Messages and agent switches in time order. Untimed messages keep their place. */
export function buildTimeline<M extends AttributedMessage>(
  messages: M[],
  events: AgentSwitchEvent[] = [],
): TimelineItem<M>[] {
  const items: TimelineItem<M>[] = messages.map((message) => ({ kind: "message", message }));
  const switches = [...events]
    .filter((e) => e && e.type === "agent_switch" && e.timestamp)
    .sort((a, b) => (a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0));
  for (const event of switches) {
    // Insert before the first timed message that comes after the switch.
    const index = items.findIndex(
      (item) => item.kind === "message" && !!item.message.timestamp && item.message.timestamp > event.timestamp,
    );
    const entry: TimelineItem<M> = { kind: "switch", event };
    if (index === -1) items.push(entry);
    else items.splice(index, 0, entry);
  }
  return items;
}

/** blocked calls keyed by tool_use_id, for marking tool cards in the turn view. */
export function blockedByToolUse(calls: BlockedCall[] = []): Record<string, BlockedCall> {
  const map: Record<string, BlockedCall> = {};
  for (const call of calls) if (call.tool_use_id) map[call.tool_use_id] = call;
  return map;
}

/**
 * The agent behind a turn's text: the first assistant reply saved at or after
 * the turn (each run saves its reply when it finishes). null while the run is
 * still going and has saved no reply yet.
 */
export function agentForTime(
  messages: AttributedMessage[] | undefined,
  timestamp?: string,
): { agentId: string | null; agentName: string } | null {
  const reply = (messages || []).find(
    (m) => m.role === "assistant" && !!m.timestamp && !!timestamp && m.timestamp >= timestamp,
  );
  return reply ? { agentId: reply.agent_id ?? null, agentName: messageAgentName(reply) } : null;
}
