/** Pure helpers for the tasks-board voice dispatcher (hooks/useVoiceDispatcher.ts).
 * No React or browser APIs, so tests/voice-dispatcher.test.cjs can run them. */

export type VoiceSpeaker = "user" | "assistant";

export interface VoiceLine {
  speaker: VoiceSpeaker;
  text: string;
  endMs: number;
}

/** A pause this long starts a new caption line for the same speaker. */
const SAME_LINE_GAP_MS = 2500;
const MAX_LINES = 40;

/** Add a transcript fragment. The model is full duplex, so both speakers can
 * be mid-sentence at once: a fragment extends that speaker's latest line
 * unless they paused or the other speaker's line came after it. */
export function appendTranscript(
  lines: VoiceLine[], speaker: VoiceSpeaker, delta: string, startMs: number, endMs: number,
): VoiceLine[] {
  if (!delta) return lines;
  const last = lines[lines.length - 1];
  if (last && last.speaker === speaker && startMs - last.endMs < SAME_LINE_GAP_MS) {
    return [...lines.slice(0, -1), { speaker, text: last.text + delta, endMs }];
  }
  return [...lines, { speaker, text: delta.trimStart(), endMs }].slice(-MAX_LINES);
}

export interface VoiceTask {
  conversation_id: string;
  title: string | null;
  prompt: string;
  column: string;
}

export type VoiceColumn = "needs_input" | "working" | "staged" | "done";

/** Board columns as the voice tools name them; every staging lane is "staged". */
export function voiceColumn(column: string): VoiceColumn {
  return column === "needs_input" || column === "working" || column === "done" ? column : "staged";
}

export function taskLabel(task: VoiceTask): string {
  return task.title || task.prompt.slice(0, 80) || "Untitled task";
}

// Filler in "the billing task" / "that PR review one" that says nothing about which task.
const STOP_WORDS = new Set(["the", "a", "an", "my", "that", "this", "task", "tasks", "one", "about", "for", "on", "of"]);

function tokens(text: string): string[] {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

/** Tasks a spoken reference could mean: an exact id, else every task whose
 * title or prompt has all the reference's words. Finished tasks only count
 * when nothing live matches, so "the billing task" means the open one. */
export function matchTasks<T extends VoiceTask>(tasks: T[], ref: string): T[] {
  const wanted = ref.trim();
  if (!wanted) return [];
  const byId = tasks.find((t) => t.conversation_id === wanted);
  if (byId) return [byId];

  const all = tokens(wanted);
  const meaningful = all.filter((w) => !STOP_WORDS.has(w));
  const words = meaningful.length ? meaningful : all;
  if (!words.length) return [];
  const matches = tasks.filter((task) => {
    const have = tokens(`${task.title || ""} ${task.prompt}`);
    return words.every((word) => have.some((h) => h.startsWith(word)));
  });
  const live = matches.filter((t) => t.column !== "done");
  return live.length ? live : matches;
}

export interface VoiceToolCall {
  callId: string;
  name: string;
  args: Record<string, unknown>;
}

/** A finished function call from a delegated backend `response.event`, or
 * null for every other event. Unparseable arguments become {} so the tool
 * can answer with a normal "missing field" error. */
export function parseToolCall(envelope: unknown): VoiceToolCall | null {
  const env = envelope as { type?: string; event?: { type?: string; item?: Record<string, unknown> } } | null;
  if (env?.type !== "response.event" || env.event?.type !== "response.output_item.done") return null;
  const item = env.event.item;
  if (!item || item.type !== "function_call") return null;
  if (typeof item.call_id !== "string" || typeof item.name !== "string") return null;
  let args: unknown = {};
  try {
    args = JSON.parse(typeof item.arguments === "string" ? item.arguments : "{}");
  } catch {
    args = {};
  }
  const isObject = !!args && typeof args === "object" && !Array.isArray(args);
  return { callId: item.call_id, name: item.name, args: isObject ? (args as Record<string, unknown>) : {} };
}
