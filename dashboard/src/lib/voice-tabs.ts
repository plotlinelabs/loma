/** One voice session per browser: a tab that starts voice tells the others,
 * and the older session ends. Pure tie-break here (tested in
 * tests/voice-chat.test.cjs); the BroadcastChannel lives in useVoiceDispatcher. */

export interface VoiceClaim {
  tab: string;
  /** Epoch ms the session started. */
  at: number;
}

/** Whether a claim from another tab ends the session in this one: the newer
 * session wins, and a tie goes to the larger tab id so exactly one survives. */
export function otherTabWins(mine: VoiceClaim, theirs: VoiceClaim): boolean {
  if (theirs.tab === mine.tab) return false;
  if (theirs.at !== mine.at) return theirs.at > mine.at;
  return theirs.tab > mine.tab;
}

export function isVoiceClaim(data: unknown): data is VoiceClaim & { type: "voice-claim" } {
  const d = data as { type?: unknown; tab?: unknown; at?: unknown } | null;
  return !!d && d.type === "voice-claim" && typeof d.tab === "string" && typeof d.at === "number";
}
