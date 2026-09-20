/**
 * Terminal-status transcript items.
 *
 * A conversation that ended in `error` or `interrupted` persists that outcome
 * on the conversation document, not as an assistant message. Both the initial
 * loader (/chat?continue=<id>) and the recovery poller must surface it the
 * same way, otherwise a page refresh silently drops the failure the user saw
 * streamed (for example the remote-worker fail-closed message).
 */
export interface TerminalStatusItem {
  role: "assistant";
  content: string;
}

export interface TerminalStatusSource {
  status?: string;
  error?: string | null;
  final_response?: string | null;
}

export const INTERRUPTED_MESSAGE =
  "The server restarted while processing your request. You can send a follow-up to continue.";

/** Reason the backend persists for a user-initiated stop (agent/active_streams.py). */
export const STOPPED_BY_USER_REASON = "Stopped by user";
export const STOPPED_BY_USER_MESSAGE =
  "*Stopped by user.* You can provide additional context or corrections below.";

export function terminalStatusItem(conversation: TerminalStatusSource): TerminalStatusItem | null {
  if (conversation.status === "interrupted") {
    const stoppedByUser = (conversation.error || "").startsWith(STOPPED_BY_USER_REASON);
    return { role: "assistant", content: stoppedByUser ? STOPPED_BY_USER_MESSAGE : INTERRUPTED_MESSAGE };
  }
  if (conversation.status === "error" && !conversation.final_response) {
    return { role: "assistant", content: `Error: ${conversation.error || "Unknown error"}` };
  }
  return null;
}

/** Append the terminal-status item unless the transcript already ends with it. */
export function withTerminalStatus<T extends { role: string; content: string }>(
  items: T[],
  conversation: TerminalStatusSource,
): (T | TerminalStatusItem)[] {
  const item = terminalStatusItem(conversation);
  if (!item) return items;
  const last = items[items.length - 1];
  if (last && last.role === item.role && last.content === item.content) return items;
  return [...items, item];
}
