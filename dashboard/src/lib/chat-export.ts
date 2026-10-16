// Self-contained (no runtime imports) so tests/chat-export.test.cjs can load it directly.

export interface ExportMessage {
  role: string;
  content: string;
  timestamp?: string | null;
  sender?: string | null;
  agent_name?: string | null;
}

export interface ExportConversation {
  conversation_id: string;
  title?: string | null;
  prompt?: string | null;
  started_at?: string | null;
  messages?: ExportMessage[] | null;
}

/** Server datetimes have no offset; they are UTC. */
function utc(value?: string | null): Date | null {
  if (!value) return null;
  const date = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(value) || !value.includes("T") ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function stamp(value?: string | null): string {
  const date = utc(value);
  return date ? date.toISOString().slice(0, 16).replace("T", " ") + " UTC" : "";
}

/** The chat as Markdown: a heading, then each message under who said it and when. */
export function conversationToMarkdown(conversation: ExportConversation, you = "You"): string {
  const title = conversation.title || conversation.prompt?.slice(0, 80) || "Loma chat";
  const lines = [`# ${title}`, ""];
  const started = stamp(conversation.started_at);
  if (started) lines.push(`_Started ${started}. Exported from Loma._`, "");
  const messages = (conversation.messages || []).filter((m) => (m.role === "user" || m.role === "assistant") && m.content?.trim());
  const senders = new Set(messages.filter((m) => m.role === "user" && m.sender).map((m) => m.sender));
  for (const message of messages) {
    const who = message.role === "user"
      ? (senders.size > 1 && message.sender ? message.sender : you)
      : (message.agent_name || "Loma");
    const when = stamp(message.timestamp);
    lines.push(`## ${who}${when ? ` · ${when}` : ""}`, "", message.content.trim(), "");
  }
  return lines.join("\n").trimEnd() + "\n";
}

/** A safe file name from the chat title. */
export function exportFileName(conversation: ExportConversation): string {
  const base = (conversation.title || conversation.prompt || "loma-chat")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60) || "loma-chat";
  return `${base}.md`;
}
