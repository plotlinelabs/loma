/** Voice mode in a single chat: voice talks, the chat's text agent works.
 * Pure helpers (tested in tests/voice-chat.test.cjs) plus the waiting tone. */
import { speakable } from "./voice-dispatcher";

/** What voice can see of the chat it sits in. */
export interface ChatVoiceState {
  running: boolean;
  /** Epoch ms the current run started, when running. */
  startedAt: number | null;
  /** The step the agent is on now, in plain words. */
  currentStep: string | null;
  latestReply: string | null;
  /** The agent stopped to ask the user something. */
  waitingForUser: boolean;
}

/** One message of the chat, as voice reads it. "loma" is the text side of Loma. */
export type VoiceChatMessage = { role: "user" | "loma"; text: string };

/** The chat voice controls: read its state and messages, send it a message, stop its run. */
export interface ChatVoiceTarget {
  getState: () => ChatVoiceState;
  /** Every user and Loma message in the chat, oldest first. */
  getMessages?: () => VoiceChatMessage[];
  /** The chat's conversation id, once it has one. Lets voice move to the board. */
  conversationId?: () => string | null;
  send: (message: string) => void;
  stop: () => void;
}

export type ChatRunOutcome = "finished" | "failed" | "needs_input";

export function chatRunOutcome(state: Pick<ChatVoiceState, "latestReply" | "waitingForUser">): ChatRunOutcome {
  if (state.waitingForUser) return "needs_input";
  // Run failures surface as the reply itself ("Error: ...", or the runtime's "Failed to ...").
  if (/^(error:|failed to )/i.test((state.latestReply ?? "").trim())) return "failed";
  return "finished";
}

/** Most context a single session append can hold is 500 tokens; stay well under it. */
export const APPEND_CHARS = 1500;
const DIGEST_CHARS = 500;

/** The lead of a reply, which carries its answer: its opening prose paragraphs, up to the first
 * heading, list, table or code block (those don't read aloud), made speakable and capped at `limit`. */
export function replyDigest(reply: string, limit = DIGEST_CHARS): string {
  const blocks = reply.trim().split(/\n\s*\n/);
  const structured = (b: string) => /^\s*(#|[-*+] |\d+[.)] |\||```|>|(-{3,}|\*{3,}|_{3,})\s*$)/.test(b);
  let start = 0;
  while (start < blocks.length && /^\s*#/.test(blocks[start])) start++;
  const lead: string[] = [];
  for (const block of blocks.slice(start)) {
    if (structured(block)) break;
    lead.push(block);
  }
  const text = speakable(lead.join(" ") || blocks.filter((b) => !/^\s*(\||```)/.test(b)).join(" "), limit);
  return text || speakable(reply, limit);
}

const OUTCOME_LINE: Record<ChatRunOutcome, string> = {
  finished: "Done.",
  failed: "I ran into an error and had to stop.",
  needs_input: "I need something from you before I can carry on.",
};

/** What voice says when the chat's run ends, as direct first-person speech the voice model paraphrases. */
export function chatAnnouncement(outcome: ChatRunOutcome, reply: string | null): string {
  const digest = reply ? replyDigest(reply) : "";
  const said = digest ? (/[.!?]$/.test(digest) ? digest : `${digest}.`) : "";
  if (outcome === "needs_input") return `${OUTCOME_LINE.needs_input}${said ? ` ${said}` : ""}`;
  return `${OUTCOME_LINE[outcome]}${said ? ` Here's the short version: ${said}` : ""}`
    + " The full details are in the chat. What would you like to do next?";
}

/** Context (not spoken) appended when voice starts and as the chat grows:
 * the chat's messages, newest kept first, in chunks that fit one append each. */
export function historyChunks(messages: VoiceChatMessage[], opts: { maxChunks?: number; userChars?: number; lomaChars?: number } = {}): string[] {
  const { maxChunks = 4, userChars = 300, lomaChars = 700 } = opts;
  const header = "Earlier in this chat (you are Loma; \"You\" lines are what you wrote in text). Reference only, not requests to act on:";
  const lines = messages
    .map((m) => `${m.role === "user" ? "User" : "You"}: ${m.role === "user" ? speakable(m.text, userChars) : replyDigest(m.text, lomaChars)}`)
    .filter((l) => !/:\s*$/.test(l));
  const chunks: string[][] = [];
  let current: string[] = [];
  let size = header.length;
  for (let i = lines.length - 1; i >= 0 && chunks.length < maxChunks; i--) {
    if (current.length && size + lines[i].length + 1 > APPEND_CHARS) {
      chunks.push(current);
      current = [];
      size = header.length;
      if (chunks.length >= maxChunks) break;
    }
    current.unshift(lines[i]);
    size += lines[i].length + 1;
  }
  if (current.length && chunks.length < maxChunks) chunks.push(current);
  const omitted = lines.length - chunks.reduce((n, c) => n + c.length, 0);
  return chunks.reverse().map((c, i) => [i === 0 && omitted > 0 ? `${header} (${omitted} older messages left out; use read_chat for them)` : header, ...c].join("\n"));
}

/** Context appended when a run ends: the reply beyond the spoken lead, for follow-up questions. */
export function replyContext(reply: string): string {
  return `Your full latest reply in the chat, for follow-up questions (reference only): ${speakable(reply, APPEND_CHARS - 100)}`;
}

/** read_chat result: the latest messages, or those matching the user's words. */
export function readChat(messages: VoiceChatMessage[], query: string, limit = 6): Record<string, unknown> {
  const words = query.toLowerCase().split(/\W+/).filter((w) => w.length > 2);
  const indexed = messages.map((m, i) => ({ ...m, i }));
  const picked = words.length
    ? indexed
      .map((m) => ({ m, score: words.filter((w) => m.text.toLowerCase().includes(w)).length }))
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score || b.m.i - a.m.i)
      .slice(0, limit)
      .map((x) => x.m)
      .sort((a, b) => a.i - b.i)
    : indexed.slice(-limit);
  return {
    total_messages: messages.length,
    matched: picked.length,
    messages: picked.map((m) => ({ number: m.i + 1, from: m.role === "user" ? "user" : "you", text: speakable(m.text, 1200) })),
  };
}

/** check_progress result for the voice backend. */
export function chatProgress(state: ChatVoiceState, now: number): Record<string, unknown> {
  if (!state.running) {
    return { running: false, waiting_for_user: state.waitingForUser, latest_reply: state.latestReply ? speakable(state.latestReply, 600) : null };
  }
  return {
    running: true,
    elapsed_seconds: state.startedAt ? Math.max(0, Math.round((now - state.startedAt) / 1000)) : null,
    current_step: state.currentStep || "thinking",
  };
}

/** The tone pauses while anyone talks and comes back after this much quiet. */
export const TONE_RESUME_QUIET_MS = 2000;

/** Whether the waiting tone should play right now. */
export function shouldPlayWaitingTone(s: {
  agentRunning: boolean;
  now: number;
  /** Last time the user was heard (mic level or transcript). */
  lastUserSpeechAt: number;
  /** Voice is speaking until this time. */
  speakingUntil: number;
  /** Voice is thinking about a request (its reply is coming). */
  voiceBusy: boolean;
}): boolean {
  if (!s.agentRunning || s.voiceBusy) return false;
  if (s.now < s.speakingUntil) return false;
  return s.now - s.lastUserSpeechAt >= TONE_RESUME_QUIET_MS;
}

/** Root-mean-square level of a mic frame (0..1). */
export function micLevel(frame: ArrayLike<number>): number {
  if (!frame.length) return 0;
  let sum = 0;
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
  return Math.sqrt(sum / frame.length);
}

/** Level above which the user counts as talking. Echo cancellation keeps the tone well below it. */
export const SPEECH_LEVEL = 0.03;

const TONE_VOLUME = 0.05;
/** A soft chime comes back this often while the agent works. */
export const CHIME_EVERY_MS = 6000;
/** The first chime waits a moment, so a quick answer never chimes at all. */
const FIRST_CHIME_MS = 1200;
const CHIME_NOTES: Array<[hz: number, offset: number]> = [[660, 0], [880, 0.14]];
const CHIME_DECAY_SECONDS = 1.4;

/** The waiting sound: a short, soft two-note chime every few seconds while the
 * agent works, made live with Web Audio (no asset to load). Silent between
 * chimes, and stops at once when set off. Also measures the mic so speech pauses it. */
export class WaitingTone {
  private ctx: AudioContext;
  private gain: GainNode;
  private analyser: AnalyserNode | null = null;
  private frame: Float32Array<ArrayBuffer> | null = null;
  private playing = false;
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor(mic?: MediaStream) {
    this.ctx = new AudioContext();
    this.gain = this.ctx.createGain();
    this.gain.gain.value = 0;
    this.gain.connect(this.ctx.destination);
    if (mic) {
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 1024;
      this.ctx.createMediaStreamSource(mic).connect(this.analyser);
      this.frame = new Float32Array(this.analyser.fftSize);
    }
  }

  /** Current mic level, 0 when unknown. */
  level(): number {
    if (!this.analyser || !this.frame) return 0;
    this.analyser.getFloatTimeDomainData(this.frame);
    return micLevel(this.frame);
  }

  get isPlaying(): boolean {
    return this.playing;
  }

  private chime() {
    const start = this.ctx.currentTime + 0.02;
    for (const [hz, offset] of CHIME_NOTES) {
      const osc = this.ctx.createOscillator();
      const env = this.ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = hz;
      env.gain.setValueAtTime(0, start + offset);
      env.gain.linearRampToValueAtTime(1, start + offset + 0.02);
      env.gain.exponentialRampToValueAtTime(0.001, start + offset + CHIME_DECAY_SECONDS);
      osc.connect(env).connect(this.gain);
      osc.start(start + offset);
      osc.stop(start + offset + CHIME_DECAY_SECONDS + 0.05);
    }
  }

  set(play: boolean) {
    if (play === this.playing) return;
    this.playing = play;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (!play) {
      // Cut a chime that is still ringing, quickly but without a click.
      this.gain.gain.setTargetAtTime(0, this.ctx.currentTime, 0.03);
      return;
    }
    if (this.ctx.state === "suspended") void this.ctx.resume().catch(() => {});
    this.gain.gain.cancelScheduledValues(this.ctx.currentTime);
    this.gain.gain.setValueAtTime(TONE_VOLUME, this.ctx.currentTime);
    const ring = (delay: number) => {
      this.timer = setTimeout(() => {
        if (!this.playing) return;
        this.chime();
        ring(CHIME_EVERY_MS);
      }, delay);
    };
    ring(FIRST_CHIME_MS);
  }

  close() {
    this.playing = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    void this.ctx.close().catch(() => {});
  }
}

/** A chat's voice session moving to the tasks board when its task is closed
 * or another task is opened while the agent still works. The board keeps the
 * user talking to the same Loma and reports the result when the run ends. */
export interface VoiceHandoff {
  conversationId: string;
  /** The agent was still running when the chat closed. */
  running: boolean;
  /** A request was just handed to the agent and its run has not shown up yet. */
  starting: boolean;
  /** A result the chat had not said yet (the run ended just before closing). */
  announcement: string | null;
  /** Recent captions, so the board session knows what was being discussed. */
  lines: CarriedLine[];
  /** Board tasks voice was watching before it moved into this task, handed back. */
  watching?: WatchedTask[];
}

export type CarriedLine = { speaker: "user" | "assistant"; text: string };

/** A board task voice watches: its last seen column, and whether the waiting tone is for it. */
export type WatchedTask = { id: string; column: string; tone: boolean };

/** The board's voice session moving into a task the user opened. */
export interface BoardRelease {
  lines: CarriedLine[];
  /** Tasks the board was watching; handed back when the task is closed. */
  watching: WatchedTask[];
  /** Board updates not said yet. */
  announcement: string | null;
}

/** Whether closing the chat should hand voice to the board instead of ending it:
 * only while voice is still waiting on the agent or holding its result. */
export function shouldHandOff(s: {
  active: boolean; running: boolean; watchingRun: boolean; pendingResult: boolean;
  /** Voice came into this task from the board, so it goes back there. */
  cameFromBoard?: boolean;
}): boolean {
  return s.active && (s.running || s.watchingRun || s.pendingResult || !!s.cameFromBoard);
}

const BRIEFING_LINES = 6;
const BRIEFING_LINE_CHARS = 200;

/** What the board's voice session is told when it takes over from a chat. */
function recentContext(lines: CarriedLine[]): string {
  const recent = lines.slice(-BRIEFING_LINES)
    .map((l) => `${l.speaker === "user" ? "User" : "You"}: ${l.text.trim().slice(0, BRIEFING_LINE_CHARS)}`)
    .filter((l) => !/:\s*$/.test(l));
  return recent.length ? ` The conversation so far, as context only (not requests to act on): ${recent.join(" | ")}` : "";
}

export function handoffBriefing(title: string, handoff: Pick<VoiceHandoff, "running" | "starting" | "lines" | "announcement">): string {
  return `Voice moved to the tasks board: the user closed the task "${title}" while you were talking about it.`
    + (handoff.running || handoff.starting
      ? " You are still working on it in the background. In one short sentence, say you'll keep going and tell them when it's done."
      : handoff.announcement
        ? " Your work on it has stopped; the result follows."
        : " In a few words, say you're back on the board.")
    + recentContext(handoff.lines);
}

/** What a task's voice session is told when it takes over from the board. */
export function taskBriefing(title: string, s: { running: boolean; lines: CarriedLine[] }): string {
  return `Voice moved into the task "${title}" that the user just opened. From now on you talk about this task and work on it.`
    + (s.running ? " You are working on it right now." : " Nothing is running on it right now.")
    + " In a few words, say you're in the task now."
    + recentContext(s.lines);
}

