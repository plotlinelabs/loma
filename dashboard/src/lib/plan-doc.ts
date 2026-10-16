// Reads a plan written to the plan-mode structure (agent/plan_mode.py) so the
// panel can show its summary, outline and phases, and say what changed between
// versions. Self-contained so tests/plan-doc.test.cjs can load it.

export interface PlanSection {
  heading: string;
  /** Stable id for the outline's links. */
  id: string;
  body: string;
}

export interface PlanDoc {
  title: string;
  /** The lead paragraph (or an older plan's **Summary:** line). */
  summary: string;
  /** The RFC meta line: **Author** … · **Status** … · **Reviewers** … · **PR** … · **Ticket** … */
  meta: { label: string; value: string }[];
  /** **Decision asked:** what the reviewer approves. */
  decision: string | null;
  /** Older plans' chips (before the RFC structure). */
  size: string | null;
  risk: string | null;
  touches: string | null;
  sections: PlanSection[];
  phases: string[];
  questions: string[];
  diagrams: number;
  /** The plan from its first ## section on: what the panel renders under its own header. */
  body: string;
}

export interface PlanDiff {
  added: string[];
  removed: string[];
  changed: string[];
  /** The one-line "what changed" note the agent puts at the top of a revision. */
  note: string | null;
}

export function slug(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "section";
}

/** Strip Markdown emphasis and links for one-line display. */
function plain(text: string): string {
  return text.replace(/\*\*|__|`/g, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1").trim();
}

/** "**Size:** M, about 2 days." from anywhere in the preamble, up to the next bold label. */
function field(preamble: string, label: string, chip = true): string | null {
  const match = new RegExp(`\\*\\*${label}:\\*\\*\\s*([\\s\\S]*?)(?=\\s*\\*\\*[A-Z][a-z]+(?: [a-z]+)?:\\*\\*|\\n\\n|$)`).exec(preamble);
  const value = match ? plain(match[1]).replace(/\s+/g, " ") : "";
  return (chip ? value.replace(/\.$/, "") : value) || null;
}

const META_LABELS = ["Author", "Status", "Reviewers", "PR", "Ticket"];

/** "**Author** Loma · **Status** Draft · …" (labels with or without a colon). */
function metaLine(preamble: string): { label: string; value: string }[] {
  const line = preamble.split("\n").find((l) => META_LABELS.filter((label) => l.includes(`**${label}`)).length >= 2);
  if (!line) return [];
  return line.split(/\s+·\s+/).map((part) => {
    const match = /^\*\*([^*:]+):?\*\*:?\s*(.*)$/.exec(part.trim());
    return match ? { label: match[1].trim(), value: plain(match[2]) } : null;
  }).filter((x): x is { label: string; value: string } => !!x && !!x.value);
}

/** Table body rows as "first cell: second cell". */
function tableRows(body: string): string[] {
  const rows = body.split("\n").filter((line) => /^\s*\|/.test(line));
  return rows.slice(1).filter((row) => !/^\s*\|[\s:|-]+\|?\s*$/.test(row)).map((row) => {
    const cells = row.split("|").slice(1, -1).map((cell) => plain(cell));
    return cells.slice(0, 2).filter(Boolean).join(": ");
  }).filter(Boolean);
}

/** Lines that are list items, without their markers. */
function listItems(body: string, numbered: boolean): string[] {
  const re = numbered ? /^\s*\d+[.)]\s+(.+)$/ : /^\s*(?:[-*]|\d+[.)])\s+(.+)$/;
  return body.split("\n").map((line) => re.exec(line)?.[1]).filter((x): x is string => !!x)
    .map((item) => plain(item.replace(/^\[[ xX]\]\s*/, "")));
}

/** Task items ("- [ ] …") in a body. */
function taskItems(body: string): string[] {
  return body.split("\n").map((line) => /^\s*[-*]\s+\[[ xX]\]\s+(.+)$/.exec(line)?.[1]).filter((x): x is string => !!x).map(plain);
}

export function parsePlan(content: string): PlanDoc {
  // Fenced blocks can hold "#" lines (comments, Mermaid); don't read them as headings.
  const lines = content.split("\n");
  let inFence: string | null = null;
  let title = "";
  const preamble: string[] = [];
  const sections: PlanSection[] = [];
  const seen = new Map<string, number>();
  let bodyStart = -1;
  for (const [index, line] of lines.entries()) {
    const fence = /^(`{3,}|~{3,})/.exec(line.trim());
    if (fence) {
      if (!inFence) inFence = fence[1];
      else if (line.trim().startsWith(inFence) && line.trim().replace(/[`~]/g, "") === "") inFence = null;
    }
    const heading = !inFence && !fence ? /^(#{1,2})\s+(.+?)\s*#*$/.exec(line) : null;
    if (heading && heading[1] === "#" && !title && sections.length === 0) {
      title = plain(heading[2]);
      continue;
    }
    if (heading && heading[1] === "##") {
      if (bodyStart < 0) bodyStart = index;
      const text = plain(heading[2]);
      const base = slug(text);
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      sections.push({ heading: text, id: n ? `${base}-${n + 1}` : base, body: "" });
      continue;
    }
    if (sections.length) sections[sections.length - 1].body += `${line}\n`;
    else preamble.push(line);
  }
  for (const section of sections) section.body = section.body.trim();
  const intro = preamble.join("\n");
  const find = (name: RegExp) => sections.find((s) => name.test(s.heading));
  const summary = field(intro, "Summary", false)
    ?? plain(intro.split(/\n\s*\n/).map((p) => p.trim()).find((p) => p && !p.startsWith("**")) ?? "");
  // Phases: an older plan's numbered Phases, else the RFC's section 13 (its PR
  // table, else its checklist). Questions: section 16 up to its follow-ups.
  const phaseSection = find(/^phases|^steps|^milestones/i) ?? find(/rollout/i);
  const phaseBody = phaseSection?.body ?? "";
  const phases = listItems(phaseBody, true).length ? listItems(phaseBody, true)
    : tableRows(phaseBody).length ? tableRows(phaseBody) : taskItems(phaseBody);
  const questionBody = (find(/question/i)?.body ?? "").split(/^\s*\*\*(?:Follow-ups|Sources)/m)[0];
  return {
    title: title || "Plan",
    summary,
    size: field(intro, "Size"),
    risk: field(intro, "Risk"),
    touches: field(intro, "Touches"),
    sections,
    meta: metaLine(intro),
    decision: field(intro, "Decision asked", false),
    phases,
    questions: listItems(questionBody, false),
    diagrams: (content.match(/^\s*(`{3,})mermaid\b/gm) || []).length,
    body: bodyStart >= 0
      ? lines.slice(bodyStart).join("\n")
      : lines.filter((line) => !/^#\s/.test(line)).join("\n").trim(),
  };
}

/** What a revision changed, section by section. */
export function diffPlans(previous: string, next: string): PlanDiff {
  const before = parsePlan(previous);
  const after = parsePlan(next);
  const old = new Map(before.sections.map((s) => [s.heading.toLowerCase(), s.body]));
  const now = new Map(after.sections.map((s) => [s.heading.toLowerCase(), s.body]));
  const norm = (text: string) => text.replace(/\s+/g, " ").trim();
  // The "**Changed:** …" line a revision carries in its header (any line before the sections).
  const header = next.split(/^##\s/m)[0].split("\n").map((l) => l.trim());
  const changedLine = header.find((l) => /^(\*\*|_)?(changed|what changed|changes|updated)\b/i.test(l)) ?? null;
  const note = changedLine ? plain(changedLine) : null;
  return {
    added: after.sections.filter((s) => !old.has(s.heading.toLowerCase())).map((s) => s.heading),
    removed: before.sections.filter((s) => !now.has(s.heading.toLowerCase())).map((s) => s.heading),
    changed: after.sections
      .filter((s) => old.has(s.heading.toLowerCase()) && norm(old.get(s.heading.toLowerCase())!) !== norm(s.body))
      .map((s) => s.heading),
    note,
  };
}
