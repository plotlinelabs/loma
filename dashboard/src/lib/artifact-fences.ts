// Fenced blocks the backend promotes to artifact cards (agent/client.py
// _detect_artifacts). Self-contained so tests/artifact-fences.test.cjs can load it.

export interface FencedArtifact {
  content: string;
}

// 3+ backticks, closed by a fence of the same length (as agent/client.py): a
// plan comes in a ````plan fence so the ```mermaid blocks inside it survive.
export const FENCED_BLOCK_RE = /(?<!`)(`{3,})(\w*)[^\S\n]*\n([\s\S]*?)\1(?!`)/g;

/** The first fence opened but not yet closed (a block still streaming in). */
export function unclosedFence(text: string): { index: number; lang: string } | null {
  const opener = /(?<!`)(`{3,})(\w*)[^\S\n]*\n/g;
  let open: RegExpExecArray | null;
  while ((open = opener.exec(text))) {
    const close = new RegExp(`(?<!\`)${open[1]}(?!\`)`, "g");
    close.lastIndex = opener.lastIndex;
    const closed = close.exec(text);
    if (!closed) return { index: open.index, lang: open[2].toLowerCase() };
    opener.lastIndex = close.lastIndex;
  }
  return null;
}
export const DESIGN_FENCE_LANGUAGES = new Set(["html", "svg", "jsx", "tsx"]);

/** Mirrors the backend's promotion threshold (agent/client.py _detect_artifacts). */
function isArtifactSized(body: string): boolean {
  const content = body.trimEnd();
  return content.length >= 200 || content.split("\n").length - 1 >= 8;
}

export type PendingArtifact = "design" | "plan" | null;

/** A block the backend will turn into an artifact card. */
function pendingKind(lang: string, body: string): PendingArtifact {
  const l = lang.toLowerCase();
  if (l === "plan") return "plan";
  return DESIGN_FENCE_LANGUAGES.has(l) && isArtifactSized(body) ? "design" : null;
}

/**
 * The backend sends a promoted code block twice: inside the message text and
 * as an artifact. Drop the copy from the text, since the card shows it. While
 * streaming, a design or plan block whose card hasn't arrived yet (unfinished,
 * or finished but ahead of its artifact event) is held back and reported as
 * `pending`, so raw source never scrolls past in the chat.
 */
export function withoutArtifactSource(
  content: string,
  artifacts: FencedArtifact[],
  streaming: boolean,
): { text: string; pending: PendingArtifact; sections: string[] } {
  const bodies = new Set(artifacts.map((a) => a.content.trimEnd()));
  let pending: PendingArtifact = null;
  let sections: string[] = [];
  let text = content.replace(FENCED_BLOCK_RE, (block, _fence: string, lang: string, body: string) => {
    if (bodies.has(body.trimEnd())) return "";
    // The artifact event follows the full text, so a finished block is
    // still unmatched for a moment: hold it back if it will be promoted.
    const kind = streaming ? pendingKind(lang, body) : null;
    if (kind) {
      pending = kind;
      if (kind === "plan") sections = planSections(body);
      return "";
    }
    return block;
  });
  const open = streaming ? unclosedFence(text) : null;
  if (open && (open.lang === "plan" || DESIGN_FENCE_LANGUAGES.has(open.lang))) {
    if (open.lang === "plan") sections = planSections(text.slice(open.index));
    text = text.slice(0, open.index);
    pending = open.lang === "plan" ? "plan" : "design";
  }
  if (text !== content) text = text.replace(/\n{3,}/g, "\n\n").trim();
  return { text, pending, sections };
}

/** The ## sections of a plan written so far, for its "Writing the plan" card. */
export function planSections(planText: string): string[] {
  const sections: string[] = [];
  let fence: string | null = null;
  for (const line of planText.split("\n").slice(1)) {
    const marker = /^\s*(`{3,})/.exec(line)?.[1];
    if (marker && !fence) fence = marker;
    else if (marker && marker.length >= fence!.length && !line.trim().slice(marker.length)) fence = null;
    else if (!fence) {
      const heading = /^##\s+(.+?)\s*#*$/.exec(line);
      if (heading) sections.push(heading[1].replace(/\*\*/g, ""));
    }
  }
  return sections;
}
