/** Pure helpers for the tasks-board voice dispatcher (hooks/useVoiceDispatcher.ts).
 * No React or browser APIs, so tests/voice-dispatcher.test.cjs can run them. */

import type { TaskStar, TaskStarUpdate } from "./api";

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

export interface VoiceLane {
  id: string;
  name: string;
}

/** A task as move_task needs it: where it is and whether it has ever run. */
export interface MovableTask extends VoiceTask {
  status: string | null;
  star?: TaskStar | null;
  human_task?: unknown;
}

export interface TaskMove {
  /** Body for PATCH /api/tasks/{id}. */
  updates: { task_status?: "todo" | "active" | "done"; task_lane?: string };
  /** When present, patch only the private bookmark instead of the task. */
  starUpdates?: TaskStarUpdate;
  /** The column's name as the user would say it. */
  destination: string;
  /** The board column the task ends up in. */
  column: string;
}

const DONE_WORDS = new Set(["done", "complete", "completed", "finished", "finish"]);
const REOPEN_WORDS = new Set(["needs input", "needs_input", "need input", "reopen", "reopened", "open"]);
const WORKING_WORDS = new Set(["working", "running", "in progress", "start", "started"]);

function findLane(lanes: VoiceLane[], wanted: string): VoiceLane | undefined {
  const exact = lanes.find((l) => l.id === wanted || l.name.toLowerCase() === wanted);
  if (exact) return exact;
  const words = tokens(wanted).filter((w) => !STOP_WORDS.has(w) && w !== "lane" && w !== "column");
  if (!words.length) return undefined;
  const close = lanes.filter((l) => {
    const have = tokens(l.name);
    return words.every((word) => have.some((h) => h.startsWith(word)));
  });
  return close.length === 1 ? close[0] : undefined;
}

/** The PATCH that moves a task to a spoken destination, or why the board does
 * not allow it. Mirrors components/tasks/transitions.ts (and the PATCH
 * handler, which stays authoritative): Working and Needs input are derived
 * from the run, so the only way into them is reopening a done task. */
export function planMove(task: MovableTask, to: string, lanes: VoiceLane[]): TaskMove | { error: string } {
  const wanted = to.trim().toLowerCase();
  if (!wanted) return { error: "Say where to move it." };
  if (task.human_task) return { error: "Use the human task response controls; moving it is not approval." };
  if (task.star) {
    if (DONE_WORDS.has(wanted)) {
      return { updates: {}, starUpdates: { done: true }, destination: "Done", column: "done" };
    }
    if (REOPEN_WORDS.has(wanted)) {
      if (task.column !== "done") return { error: "That bookmark is not done." };
      const source = task.star.source_column;
      const column = source === "working" || source === "needs_input" ? source : task.star.lane;
      const destination = column === "working" ? "Working" : column === "needs_input" ? "Needs input"
        : lanes.find((l) => l.id === column)?.name || column;
      return { updates: {}, starUpdates: { done: false }, destination, column };
    }
    const lane = findLane(lanes, wanted);
    if (!lane) return { error: `Choose a bookmark lane or Done: ${lanes.map((l) => l.name).join(", ")}.` };
    return { updates: {}, starUpdates: { lane: lane.id, done: false }, destination: lane.name, column: lane.id };
  }
  const from = voiceColumn(task.column);
  const isDraft = !task.status;

  if (DONE_WORDS.has(wanted)) {
    if (from === "done") return { error: "That task is already done." };
    if (from === "staged" && isDraft) return { error: "That task is a draft that has never run, so it can't be marked done." };
    return { updates: { task_status: "done" }, destination: "Done", column: "done" };
  }
  if (WORKING_WORDS.has(wanted)) {
    return { error: "A task is in Working only while it runs. Use start_task for a draft, or send a follow-up message to resume a task." };
  }
  if (REOPEN_WORDS.has(wanted)) {
    if (from !== "done") return { error: "Only a done task can be reopened. A task needs input when its run stops to ask." };
    return { updates: { task_status: "active" },
      destination: task.status === "running" ? "Working" : "Needs input",
      column: task.status === "running" ? "working" : "needs_input" };
  }
  const lane = findLane(lanes, wanted);
  if (!lane) {
    return { error: `No column called "${to.trim()}". Columns to move to: ${[...lanes.map((l) => l.name), "Done"].join(", ")}.` };
  }
  if (from === "working") return { error: "That task is running. Stop it first, or wait for it to finish." };
  if (task.column === lane.id) return { error: `That task is already in ${lane.name}.` };
  return {
    updates: from === "staged" ? { task_lane: lane.id } : { task_status: "todo", task_lane: lane.id },
    destination: lane.name,
    column: lane.id,
  };
}

/** Starting a saved draft uses its stored instructions, never the spoken title. */
export function startTaskError(task: MovableTask & { task_status?: string | null }): string | null {
  if (task.human_task) return "Use the human task response controls; it cannot run as an agent.";
  if (task.status === "running") return "That task is already running.";
  if (task.task_status !== "todo" || task.status) return "That task has already run. Send a follow-up message to continue it.";
  if (!task.prompt.trim()) return "That draft has no instructions. Add details before starting it.";
  return null;
}

export type RunOutcome = "finished" | "failed" | "needs_input" | "done";

export interface TaskTransition<T extends VoiceTask = VoiceTask> {
  task: T;
  outcome: RunOutcome;
}

/** Tasks that stopped running since the last look at the board. `watched`
 * maps a task id to the column it was last seen in; it is updated in place.
 * Only a task seen in Working can be announced, so a board that was already
 * full of finished tasks when voice started stays quiet. */
export function detectFinished<T extends VoiceTask & { status: string | null }>(
  watched: Map<string, string>, tasks: T[],
): TaskTransition<T>[] {
  const finished: TaskTransition<T>[] = [];
  const present = new Set<string>();
  for (const task of tasks) {
    const column = voiceColumn(task.column);
    const before = watched.get(task.conversation_id);
    present.add(task.conversation_id);
    watched.set(task.conversation_id, column);
    if (before !== "working" || column === "working") continue;
    // Parked in a lane mid-run is the user's own move, not news.
    if (column === "staged") continue;
    const outcome: RunOutcome = column === "done" ? "done"
      : task.status === "completed" ? "finished"
      : task.status === "error" || task.status === "failed" ? "failed"
      : "needs_input";
    finished.push({ task, outcome });
  }
  // Removed from the board (or filtered out): forget it rather than announce.
  watched.forEach((_, id) => { if (!present.has(id)) watched.delete(id); });
  return finished;
}

const OUTCOME_PHRASE: Record<RunOutcome, string> = {
  finished: "just finished",
  failed: "stopped with an error",
  needs_input: "stopped and is waiting for you",
  done: "was marked done",
};

/** Appended context is capped at 500 tokens; stay well inside it. */
const ANNOUNCE_REPLY_CHARS = 280;
const MAX_ANNOUNCED = 3;

/** Plain speakable text from the start of an agent reply. */
export function speakable(reply: string, limit = ANNOUNCE_REPLY_CHARS): string {
  const text = reply
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+/g, " ")
    .replace(/[*`]/g, "")
    .replace(/[#>|]/g, " ")
    .replace(/\s+/g, " ")
    .replace(/ ([.,;:!?])/g, "$1")
    .trim();
  if (text.length <= limit) return text;
  const cut = text.slice(0, limit);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), limit - 40))}...`;
}

/** What the voice model should say about tasks that stopped running. One
 * task gets a line from its reply; several are grouped into one sentence. */
export function announcement(items: Array<{ title: string; outcome: RunOutcome; reply?: string | null }>): string {
  if (!items.length) return "";
  if (items.length === 1) {
    const { title, outcome, reply } = items[0];
    const said = reply ? speakable(reply) : "";
    return `Update from the board: the task "${title}" ${OUTCOME_PHRASE[outcome]}.`
      + (said ? ` It says: ${/[.!?]$/.test(said) ? said : `${said}.`}` : "")
      + " Want to see it?";
  }
  const named = items.slice(0, MAX_ANNOUNCED).map((i) => `"${i.title}" ${OUTCOME_PHRASE[i.outcome]}`);
  const rest = items.length - named.length;
  return `Update from the board: ${named.join(", ")}${rest > 0 ? `, and ${rest} more stopped` : ""}. Want to see any of them?`;
}
