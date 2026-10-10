/** Links voice mode may open: only those rendered in the open task's own
 * replies. The matching is pure (tested in tests/voice-links.test.cjs); the
 * functions at the bottom read the DOM and open the tab. */

export type LinkKind =
  | "pull request" | "GitHub issue" | "Google Doc" | "Google Sheet" | "Google Slides"
  | "Drive file" | "Linear ticket" | "Notion page" | "link";

/** A link as it appears in a reply; `reply` counts replies from the top. */
export interface RawLink {
  href: string;
  label: string;
  reply: number;
}

export interface TaskLink {
  href: string;
  label: string;
  kind: LinkKind;
  /** Order of the latest mention, so "the latest link" means the newest. */
  lastSeen: number;
}

export type LinkChoice =
  | { link: TaskLink }
  | { none: string }
  | { ambiguous: TaskLink[] };

const LABEL_LIMIT = 120;

/** The absolute http(s) URL, normalised, or null for anything else
 * (relative paths, javascript:, data:, file:, embedded credentials). */
export function safeLinkUrl(raw: string): string | null {
  const value = (raw || "").trim();
  if (!/^https?:\/\//i.test(value) || /[\s\u0000-\u001f\u007f]/.test(value)) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (!url.hostname || url.username || url.password) return null;
  return url.href;
}

export function linkKind(href: string): LinkKind {
  const url = new URL(href);
  const host = url.hostname.replace(/^www\./, "");
  const path = url.pathname;
  if (host === "github.com" && /\/pull\/\d+/.test(path)) return "pull request";
  if (host === "github.com" && /\/issues\/\d+/.test(path)) return "GitHub issue";
  if (host === "docs.google.com" && path.startsWith("/document/")) return "Google Doc";
  if (host === "docs.google.com" && path.startsWith("/spreadsheets/")) return "Google Sheet";
  if (host === "docs.google.com" && path.startsWith("/presentation/")) return "Google Slides";
  if (host === "drive.google.com") return "Drive file";
  if (host === "linear.app" && path.includes("/issue/")) return "Linear ticket";
  if (host === "notion.so" || host.endsWith(".notion.so") || host.endsWith(".notion.site")) return "Notion page";
  return "link";
}

/** host/path, for a link whose reply showed only the raw URL. */
export function shortUrl(href: string): string {
  const url = new URL(href);
  const text = `${url.hostname.replace(/^www\./, "")}${url.pathname === "/" ? "" : url.pathname}`;
  return text.length > LABEL_LIMIT ? `${text.slice(0, LABEL_LIMIT - 3)}...` : text;
}

/** Valid links in order of first appearance, one per URL. A repeat keeps the
 * more descriptive label and moves the link's recency forward. */
export function collectLinks(raw: RawLink[]): TaskLink[] {
  const byHref = new Map<string, TaskLink>();
  raw.forEach((item, position) => {
    const href = safeLinkUrl(item.href);
    if (!href) return;
    const text = (item.label || "").replace(/\s+/g, " ").trim().slice(0, LABEL_LIMIT);
    const label = text && safeLinkUrl(text) !== href ? text : "";
    const seen = byHref.get(href);
    if (seen) {
      seen.lastSeen = position;
      if (!seen.label) seen.label = label;
      return;
    }
    byHref.set(href, { href, label, kind: linkKind(href), lastSeen: position });
  });
  return Array.from(byHref.values()).map((l) => ({ ...l, label: l.label || shortUrl(l.href) }));
}

const ORDINALS: Record<string, number> = {
  first: 1, second: 2, third: 3, fourth: 4, fifth: 5, sixth: 6, seventh: 7, eighth: 8, ninth: 9, tenth: 10,
  "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6, "7th": 7, "8th": 8, "9th": 9, "10th": 10,
};
const NUMBER_WORDS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
};
const LATEST = ["last", "latest", "newest", "recent"];
// Spoken kind words, checked in order; "doc" alone could be any document.
const KIND_WORDS: Array<[RegExp, LinkKind[]]> = [
  [/\b(pr|prs|pull requests?)\b/, ["pull request"]],
  [/\b(spreadsheets?|sheets?)\b/, ["Google Sheet"]],
  [/\b(slides?|deck|presentation)\b/, ["Google Slides"]],
  [/\b(linear|tickets?)\b/, ["Linear ticket"]],
  [/\bissues?\b/, ["GitHub issue", "Linear ticket"]],
  [/\bnotion\b/, ["Notion page"]],
  [/\bdrive\b/, ["Drive file"]],
  [/\b(docs?|documents?)\b/, ["Google Doc", "Notion page"]],
];
const FILLER = new Set([
  "open", "the", "a", "an", "that", "this", "it", "link", "links", "url", "one", "please", "to", "in", "new", "tab",
  "for", "me", "of", "on", "can", "you", "show", "go", "number", "option", "choice", "most", "pr", "prs", "pull",
  "request", "requests", "doc", "docs", "document", "documents", "sheet", "sheets", "spreadsheet", "spreadsheets",
  "slide", "slides", "deck", "presentation", "ticket", "tickets", "issue", "issues", "linear", "notion", "drive",
  ...Object.keys(ORDINALS), ...Object.keys(NUMBER_WORDS), ...LATEST,
]);

function words(text: string): string[] {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}

/** The position a reference asks for ("the second", "link 2", "number three")
 * and the word that said it. A bare number elsewhere ("PR 123") is a search word. */
function ordinalOf(text: string, said: string[]): { position: number; word: string } | null {
  for (const w of said) if (ORDINALS[w]) return { position: ORDINALS[w], word: w };
  const m = text.match(/\b(?:number|option|link|choice)\s+([a-z0-9]+)\b/);
  const word = m ? m[1] : said.length === 1 ? said[0] : "";
  if (NUMBER_WORDS[word]) return { position: NUMBER_WORDS[word], word };
  if (/^\d{1,2}$/.test(word)) return { position: Number(word), word };
  return null;
}

/** Which link a spoken reference means. Ordinals count the links as listed
 * (or the links of the named kind); several plausible links are never guessed. */
export function resolveLinkRef(links: TaskLink[], ref: string): LinkChoice {
  if (!links.length) return { none: "This task's replies have no links." };
  const text = (ref || "").toLowerCase().trim();
  const said = words(text);

  let pool = links;
  for (const [pattern, kinds] of KIND_WORDS) {
    if (!pattern.test(text)) continue;
    pool = links.filter((l) => kinds.includes(l.kind));
    if (!pool.length) return { none: `This task's replies have no ${kinds[0]} link.` };
    break;
  }

  const ordinal = ordinalOf(text, said);
  const search = said.filter((w) => !FILLER.has(w) && w !== ordinal?.word);
  if (search.length) {
    pool = pool.filter((l) => {
      const url = new URL(l.href);
      const have = words(`${l.label} ${url.hostname} ${url.pathname}`);
      return search.every((w) => have.some((h) => h.startsWith(w)));
    });
    if (!pool.length) return { none: `No link in this task's replies matches "${(ref || "").trim()}".` };
  }

  if (said.some((w) => LATEST.includes(w))) {
    return { link: pool.reduce((a, b) => (b.lastSeen > a.lastSeen ? b : a)) };
  }
  if (ordinal) {
    const picked = pool[ordinal.position - 1];
    if (picked) return { link: picked };
    return { none: `This task has only ${pool.length} matching link${pool.length === 1 ? "" : "s"}.` };
  }
  return pool.length === 1 ? { link: pool[0] } : { ambiguous: pool };
}

// ── Browser ─────────────────────────────────────────────────────────────

const DRAWER = "[data-task-drawer]";

/** Links in the open task's replies, or null when no task chat is open. Only
 * the agent's rendered reply text counts: not the user's messages, tool steps or code. */
export function readTaskLinks(): { title: string; links: RawLink[] } | null {
  if (typeof document === "undefined") return null;
  const drawer = document.querySelector<HTMLElement>(DRAWER);
  const scroller = drawer?.querySelector<HTMLElement>('[data-slot="chat-messages"]');
  if (!drawer || !scroller) return null;
  const title = drawer.querySelector<HTMLElement>('[data-slot="sheet-title"]')?.innerText.trim() || "this task";
  const links: RawLink[] = [];
  scroller.querySelectorAll<HTMLElement>('[data-chat-role="assistant"] [data-chat-reply]').forEach((reply, index) => {
    reply.querySelectorAll<HTMLAnchorElement>("a[href]").forEach((a) => {
      // The raw attribute, so a relative href is rejected rather than resolved against this page.
      links.push({ href: a.getAttribute("href") || "", label: a.textContent || "", reply: index });
    });
  });
  return { title, links };
}

/** Open a validated link in a new tab. False when the browser blocked the
 * popup (voice runs outside a click), so the caller can offer a tap instead. */
export function openLinkInNewTab(href: string): boolean {
  const safe = safeLinkUrl(href);
  if (!safe || typeof window === "undefined") return false;
  const tab = window.open("about:blank", "_blank");
  if (!tab) return false;
  try {
    tab.opener = null;
    const meta = tab.document.createElement("meta");
    meta.name = "referrer";
    meta.content = "no-referrer";
    tab.document.head?.appendChild(meta);
    tab.location.replace(safe);
    return true;
  } catch {
    tab.close();
    return false;
  }
}
