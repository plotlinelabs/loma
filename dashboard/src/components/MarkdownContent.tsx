"use client";

import React, { useMemo } from "react";

/**
 * Lightweight Markdown + Slack mrkdwn renderer for chat messages.
 *
 * Supports:
 * - Standard Markdown: headings, **bold**, _italic_, ~~strikethrough~~,
 *   inline code, fenced code blocks, lists, [links](url), paragraphs,
 *   GFM tables (pipe syntax with optional column alignment),
 *   inline images ![alt](url).
 * - Slack mrkdwn: *bold*, _italic_, ~strikethrough~, `code`,
 *   <url|label> links, <url> auto-links, bullet lists (• or -).
 *
 * No external dependencies — keeps the bundle small.
 */

interface MarkdownContentProps {
  content: string;
  className?: string;
}

// ── Inline token types ─────────────────────────────────────────────────────────

type InlineToken =
  | { type: "text"; value: string }
  | { type: "code"; value: string }
  | { type: "bold"; children: InlineToken[] }
  | { type: "italic"; children: InlineToken[] }
  | { type: "strike"; children: InlineToken[] }
  | { type: "link"; href: string; label: string }
  | { type: "image"; src: string; alt: string };

// ── Inline tokenizer (multi-pass) ──────────────────────────────────────────

/**
 * Tokenize inline formatting. We process in priority order:
 * 1. Inline code (`...`) — no nested formatting
 * 2. Slack links: <url|label> (with display text) and <url> (bare auto-links)
 * 3a. Markdown images: ![alt](url)
 * 3b. Markdown links: [label](url)
 * 4. Bold (**text** then *text*)
 * 5. Italic (_text_)
 * 6. Strikethrough (~~text~~ then ~text~)
 */
function tokenizeInline(text: string): InlineToken[] {
  if (!text) return [];

  // Step 1: Split by inline code
  let tokens = splitByPattern(text, /`([^`\n]+)`/g, (match) => ({
    type: "code" as const,
    value: match[1],
  }));

  // Step 2a: Process Slack links with label <url|label>
  tokens = expandTextTokens(tokens, /<(https?:\/\/[^|>]+)\|([^>]+)>/g, (match) => ({
    type: "link" as const,
    href: match[1],
    label: match[2],
  }));

  // Step 2b: Process bare Slack auto-links <url> (no pipe/label)
  tokens = expandTextTokens(tokens, /<(https?:\/\/[^>\s]+)>/g, (match) => ({
    type: "link" as const,
    href: match[1],
    label: match[1],
  }));

  // Step 3a: Process Markdown images ![alt](url) — BEFORE links to avoid conflict
  tokens = expandTextTokens(tokens, /!\[([^\]]*)\]\(([^)]+)\)/g, (match) => ({
    type: "image" as const,
    alt: match[1],
    src: match[2],
  }));

  // Step 3b: Process Markdown links [label](url)
  tokens = expandTextTokens(tokens, /\[([^\]]+)\]\(([^)]+)\)/g, (match) => ({
    type: "link" as const,
    href: match[2],
    label: match[1],
  }));

  // Step 4: Bold — first ** (Markdown), then * (Slack)
  tokens = expandTextTokens(tokens, /\*\*(.+?)\*\*/g, (match) => ({
    type: "bold" as const,
    children: tokenizeInline(match[1]),
  }));
  tokens = expandTextTokens(
    tokens,
    /(?:^|(?<=\s|[.,;:!?({\[]))\*([^*\n]+)\*(?=\s|[.,;:!?)}\]]|$)/gm,
    (match) => ({
      type: "bold" as const,
      children: tokenizeInline(match[1]),
    })
  );

  // Step 5: Italic _text_
  tokens = expandTextTokens(tokens, /(?:^|(?<=\s|[.,;:!?({\[]))_([^_\n]+)_(?=\s|[.,;:!?)}\]]|$)/gm, (match) => ({
    type: "italic" as const,
    children: tokenizeInline(match[1]),
  }));

  // Step 6: Strikethrough — first ~~ (Markdown), then ~ (Slack)
  tokens = expandTextTokens(tokens, /~~(.+?)~~/g, (match) => ({
    type: "strike" as const,
    children: tokenizeInline(match[1]),
  }));
  tokens = expandTextTokens(
    tokens,
    /(?:^|(?<=\s|[.,;:!?({\[]))~([^~\n]+)~(?=\s|[.,;:!?)}\]]|$)/gm,
    (match) => ({
      type: "strike" as const,
      children: tokenizeInline(match[1]),
    })
  );

  return tokens;
}

/**
 * Split a raw string by a regex pattern into a mix of text and matched tokens.
 */
function splitByPattern(
  text: string,
  pattern: RegExp,
  toToken: (match: RegExpExecArray) => InlineToken
): InlineToken[] {
  const tokens: InlineToken[] = [];
  let lastIndex = 0;
  // Reset regex state
  pattern.lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      tokens.push({ type: "text", value: text.slice(lastIndex, match.index) });
    }
    tokens.push(toToken(match));
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    tokens.push({ type: "text", value: text.slice(lastIndex) });
  }

  return tokens;
}

/**
 * For each "text" token in the list, apply a pattern and expand matches
 * into new tokens. Non-text tokens are passed through unchanged.
 */
function expandTextTokens(
  tokens: InlineToken[],
  pattern: RegExp,
  toToken: (match: RegExpExecArray) => InlineToken
): InlineToken[] {
  const result: InlineToken[] = [];
  for (const token of tokens) {
    if (token.type === "text") {
      result.push(...splitByPattern(token.value, pattern, toToken));
    } else {
      result.push(token);
    }
  }
  return result;
}

// ── Inline renderer ────────────────────────────────────────────────────────

function renderTokens(tokens: InlineToken[]): (string | React.ReactElement)[] {
  let key = 0;
  return tokens.map((token) => {
    switch (token.type) {
      case "text":
        return token.value;
      case "code":
        return (
          <code
            key={key++}
            className="bg-gray-100 text-brand-700 text-xs px-1.5 py-0.5 rounded font-mono"
          >
            {token.value}
          </code>
        );
      case "bold":
        return <strong key={key++}>{renderTokens(token.children)}</strong>;
      case "italic":
        return <em key={key++}>{renderTokens(token.children)}</em>;
      case "strike":
        return <del key={key++}>{renderTokens(token.children)}</del>;
      case "link":
        return (
          <a
            key={key++}
            href={token.href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand-600 underline hover:text-brand-800 break-all"
          >
            {token.label}
          </a>
        );
      case "image":
        return (
          <span key={key++} className="block my-2">
            <img
              src={token.src}
              alt={token.alt || "Image"}
              className="max-w-full rounded-lg shadow-sm border border-gray-200"
              style={{ maxHeight: "480px", objectFit: "contain" }}
              loading="lazy"
            />
          </span>
        );
    }
  });
}

function renderInline(text: string): (string | React.ReactElement)[] {
  return renderTokens(tokenizeInline(text));
}

// ── Table helpers ──────────────────────────────────────────────────────────

type TableAlignment = "left" | "center" | "right" | null;

/** Parse a GFM separator row (e.g. |:---|:---:|---:| ) to extract column alignments. */
function parseAlignments(separatorLine: string): TableAlignment[] {
  return parsePipeRow(separatorLine).map((cell) => {
    const trimmed = cell.trim();
    const left = trimmed.startsWith(":");
    const right = trimmed.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    return "left";
  });
}

/** Split a pipe-delimited row into cell strings, trimming the outer pipes. */
function parsePipeRow(line: string): string[] {
  let trimmed = line.trim();
  // Remove leading and trailing pipes
  if (trimmed.startsWith("|")) trimmed = trimmed.slice(1);
  if (trimmed.endsWith("|")) trimmed = trimmed.slice(0, -1);
  return trimmed.split("|").map((c) => c.trim());
}

/** Check if a line looks like a GFM table separator (|---|---|). */
function isTableSeparator(line: string): boolean {
  const trimmed = line.trim();
  // Must contain at least one pipe and consist of pipes, dashes, colons, and spaces
  return /^\|?[\s:]*-{1,}[\s:]*(?:\|[\s:]*-{1,}[\s:]*)+\|?$/.test(trimmed);
}

/** Check if a line looks like a pipe table row. */
function isTableRow(line: string): boolean {
  const trimmed = line.trim();
  // A table row must contain at least one pipe that isn't inside code backticks
  // and should start or contain a pipe character
  return trimmed.includes("|") && (trimmed.startsWith("|") || trimmed.indexOf("|") > 0);
}

// ── Block-level parsing ────────────────────────────────────────────────────

interface Block {
  type: "heading" | "code" | "ul" | "ol" | "paragraph" | "table";
  level?: number; // heading level (1-6)
  lang?: string; // code block language
  items?: string[]; // list items
  text?: string; // heading / paragraph text
  code?: string; // code block content
  // Table fields
  headers?: string[];
  alignments?: TableAlignment[];
  rows?: string[][];
}

function parseBlocks(md: string): Block[] {
  const lines = md.split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    const codeMatch = line.match(/^```(\w*)/);
    if (codeMatch) {
      const lang = codeMatch[1] || "";
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      blocks.push({ type: "code", lang, code: codeLines.join("\n") });
      continue;
    }

    // Heading
    const headingMatch = line.match(/^(#{1,6})\s+(.+)/);
    if (headingMatch) {
      blocks.push({
        type: "heading",
        level: headingMatch[1].length,
        text: headingMatch[2],
      });
      i++;
      continue;
    }

    // GFM Table: detect header row followed by separator row
    if (
      isTableRow(line) &&
      i + 1 < lines.length &&
      isTableSeparator(lines[i + 1])
    ) {
      const headers = parsePipeRow(line);
      const alignments = parseAlignments(lines[i + 1]);
      const rows: string[][] = [];
      i += 2; // skip header and separator
      while (i < lines.length && isTableRow(lines[i]) && !isTableSeparator(lines[i])) {
        rows.push(parsePipeRow(lines[i]));
        i++;
      }
      blocks.push({ type: "table", headers, alignments, rows });
      continue;
    }

    // Unordered list (supports -, and • bullet markers)
    // Note: * is NOT treated as a list marker since it conflicts with Slack bold
    if (/^[\-•]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[\-•]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[\-•]\s+/, ""));
        i++;
      }
      blocks.push({ type: "ul", items });
      continue;
    }

    // Ordered list
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s+/, ""));
        i++;
      }
      blocks.push({ type: "ol", items });
      continue;
    }

    // Blank line — skip
    if (line.trim() === "") {
      i++;
      continue;
    }

    // Paragraph — collect consecutive non-blank, non-special lines
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !lines[i].match(/^```/) &&
      !lines[i].match(/^#{1,6}\s/) &&
      !lines[i].match(/^[\-•]\s+/) &&
      !lines[i].match(/^\d+\.\s+/) &&
      // Don't swallow table rows into paragraphs
      !(isTableRow(lines[i]) && i + 1 < lines.length && isTableSeparator(lines[i + 1]))
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length > 0) {
      blocks.push({ type: "paragraph", text: paraLines.join("\n") });
    }
  }

  return blocks;
}

// ── Renderer ───────────────────────────────────────────────────────────────

function renderBlock(block: Block, key: number): React.ReactElement {
  switch (block.type) {
    case "heading": {
      const Tag = `h${Math.min(block.level || 1, 6)}` as "h1" | "h2" | "h3" | "h4" | "h5" | "h6";
      const sizes: Record<number, string> = {
        1: "text-lg font-bold mt-4 mb-2",
        2: "text-base font-bold mt-3 mb-1.5",
        3: "text-sm font-semibold mt-2 mb-1",
        4: "text-sm font-semibold mt-2 mb-1",
        5: "text-xs font-semibold mt-1 mb-0.5",
        6: "text-xs font-semibold mt-1 mb-0.5",
      };
      return (
        <Tag key={key} className={sizes[block.level || 1]}>
          {renderInline(block.text || "")}
        </Tag>
      );
    }

    case "code":
      return (
        <div key={key} className="my-2">
          {block.lang && (
            <div className="text-[10px] font-mono text-[#8C857D] bg-[#0A0A0A] rounded-t-lg px-3 py-1">
              {block.lang}
            </div>
          )}
          <pre
            className={`bg-[#0A0A0A] text-[#E8E4DD] text-xs p-3 overflow-x-auto font-mono leading-relaxed ${
              block.lang ? "rounded-b-lg" : "rounded-lg"
            }`}
          >
            <code>{block.code}</code>
          </pre>
        </div>
      );

    case "table": {
      const headers = block.headers || [];
      const alignments = block.alignments || [];
      const rows = block.rows || [];

      const alignClass = (idx: number): string => {
        const a = alignments[idx];
        if (a === "center") return "text-center";
        if (a === "right") return "text-right";
        return "text-left";
      };

      return (
        <div key={key} className="my-2 overflow-x-auto rounded-lg border border-gray-200">
          <table className="min-w-full text-xs">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                {headers.map((h, hi) => (
                  <th
                    key={hi}
                    className={`px-3 py-2 font-semibold text-gray-700 ${alignClass(hi)}`}
                  >
                    {renderInline(h)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map((row, ri) => (
                <tr key={ri} className={ri % 2 === 1 ? "bg-gray-50/50" : ""}>
                  {headers.map((_, ci) => (
                    <td
                      key={ci}
                      className={`px-3 py-1.5 text-gray-700 ${alignClass(ci)}`}
                    >
                      {renderInline(row[ci] || "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }

    case "ul":
      return (
        <ul key={key} className="list-disc list-inside my-1 space-y-0.5">
          {block.items?.map((item, j) => (
            <li key={j} className="text-sm">
              {renderInline(item)}
            </li>
          ))}
        </ul>
      );

    case "ol":
      return (
        <ol key={key} className="list-decimal list-inside my-1 space-y-0.5">
          {block.items?.map((item, j) => (
            <li key={j} className="text-sm">
              {renderInline(item)}
            </li>
          ))}
        </ol>
      );

    case "paragraph":
    default:
      return (
        <p key={key} className="my-1 text-sm leading-relaxed">
          {renderInline(block.text || "")}
        </p>
      );
  }
}

export default function MarkdownContent({ content, className }: MarkdownContentProps) {
  const blocks = useMemo(() => parseBlocks(content), [content]);

  return (
    <div className={`break-words ${className ?? ""}`}>
      {blocks.map((block, i) => renderBlock(block, i))}
    </div>
  );
}
