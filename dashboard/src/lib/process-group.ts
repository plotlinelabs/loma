/**
 * A reply's process (thoughts and tool steps) folds into one group, so the
 * text Loma writes reads as one answer instead of sitting between rows.
 */

export interface ProcessStep {
  status: "running" | "done" | "error";
}

export interface ProcessItem {
  role: string;
  steps?: ProcessStep[];
  thinkingSeconds?: number;
}

export function isProcessItem(item: { role: string }): boolean {
  return item.role === "thinking" || item.role === "steps";
}

/** The reply's process items, in order. The group renders where the first one was. */
export function processIndices(items: { role: string }[], indices: number[]): number[] {
  return indices.filter((i) => items[i] && isProcessItem(items[i]));
}

export interface ProcessCounts {
  thoughts: number;
  steps: number;
  failed: number;
  running: boolean;
  /** Total thinking time, when every thought has one. */
  thoughtSeconds?: number;
}

export function processCounts(items: ProcessItem[]): ProcessCounts {
  const thoughts = items.filter((item) => item.role === "thinking");
  const steps = items.flatMap((item) => (item.role === "steps" ? item.steps || [] : []));
  const timed = thoughts.every((item) => typeof item.thinkingSeconds === "number");
  return {
    thoughts: thoughts.length,
    steps: steps.length,
    failed: steps.filter((step) => step.status === "error").length,
    running: steps.some((step) => step.status === "running"),
    thoughtSeconds: thoughts.length && timed
      ? thoughts.reduce((sum, item) => sum + (item.thinkingSeconds || 0), 0)
      : undefined,
  };
}

export const stepsWord = (n: number) => `${n} ${n === 1 ? "step" : "steps"}`;

/** The folded line once the reply is done, e.g. "Thought and ran 6 steps · 1 failed". */
export function processLabel(counts: ProcessCounts): string {
  let label: string;
  if (counts.thoughts && counts.steps) label = `Thought and ran ${stepsWord(counts.steps)}`;
  else if (counts.steps) label = `Ran ${stepsWord(counts.steps)}`;
  else if (counts.thoughtSeconds) label = `Thought for ${counts.thoughtSeconds}s`;
  else label = "Thought process";
  return counts.failed ? `${label} · ${counts.failed} failed` : label;
}
