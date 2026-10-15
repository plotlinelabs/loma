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

/** The chat voice controls: read its state, send it a message, stop its run. */
export interface ChatVoiceTarget {
  getState: () => ChatVoiceState;
  /** The chat's conversation id, once it has one. Lets voice move to the board. */
  conversationId?: () => string | null;
  send: (message: string) => void;
  stop: () => void;
}

export type ChatRunOutcome = "finished" | "failed" | "needs_input";

export function chatRunOutcome(state: Pick<ChatVoiceState, "latestReply" | "waitingForUser">): ChatRunOutcome {
  if (state.waitingForUser) return "needs_input";
  if (/^error:/i.test((state.latestReply ?? "").trim())) return "failed";
  return "finished";
}

const OUTCOME_LINE: Record<ChatRunOutcome, string> = {
  finished: "the agent just finished",
  failed: "the agent stopped with an error",
  needs_input: "the agent stopped and has a question for the user",
};

/** What the voice model is told when the chat's run ends. */
export function chatAnnouncement(outcome: ChatRunOutcome, reply: string | null): string {
  const said = reply ? speakable(reply, 500) : "";
  return `Update from the chat: ${OUTCOME_LINE[outcome]}.`
    + (said ? ` Its reply starts: ${said}` : "")
    + " Give the gist in one or two sentences and say the full answer is in the chat.";
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

const TONE_VOLUME = 0.035;
const FADE_SECONDS = 0.25;

/** A soft looping pad, made live with Web Audio (no asset to load), that
 * fades in and out. Also measures the mic so speech pauses it at once. */
export class WaitingTone {
  private ctx: AudioContext;
  private gain: GainNode;
  private analyser: AnalyserNode | null = null;
  private frame: Float32Array<ArrayBuffer> | null = null;
  private playing = false;

  constructor(mic?: MediaStream) {
    this.ctx = new AudioContext();
    this.gain = this.ctx.createGain();
    this.gain.gain.value = 0;
    this.gain.connect(this.ctx.destination);
    // Two quiet tones a fifth apart, slowly breathing in volume.
    const pulse = this.ctx.createGain();
    pulse.gain.value = 0.6;
    pulse.connect(this.gain);
    const lfo = this.ctx.createOscillator();
    const depth = this.ctx.createGain();
    lfo.frequency.value = 0.35;
    depth.gain.value = 0.4;
    lfo.connect(depth).connect(pulse.gain);
    lfo.start();
    for (const hz of [220, 330]) {
      const osc = this.ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.value = hz;
      osc.connect(pulse);
      osc.start();
    }
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

  set(play: boolean) {
    if (play === this.playing) return;
    this.playing = play;
    if (play && this.ctx.state === "suspended") void this.ctx.resume().catch(() => {});
    this.gain.gain.setTargetAtTime(play ? TONE_VOLUME : 0, this.ctx.currentTime, FADE_SECONDS / 3);
  }

  close() {
    this.playing = false;
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
      ? " Its agent is still working. In one short sentence, say you'll keep an eye on it and tell them when it's done."
      : handoff.announcement
        ? " Its agent has stopped; its result follows."
        : " In a few words, say you're back on the board.")
    + recentContext(handoff.lines);
}

/** What a task's voice session is told when it takes over from the board. */
export function taskBriefing(title: string, s: { running: boolean; lines: CarriedLine[] }): string {
  return `Voice moved into the task "${title}" that the user just opened. From now on you talk about this task, and its agent does the work.`
    + (s.running ? " Its agent is working right now." : " Its agent is not working right now.")
    + " In a few words, say you're in the task now."
    + recentContext(s.lines);
}

