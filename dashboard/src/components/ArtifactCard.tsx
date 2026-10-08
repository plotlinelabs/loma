"use client";

import { RiArrowRightSLine, RiListCheck3 } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Artifact } from "./ArtifactViewer";
import { PlanStatusDot, type PlanStatus } from "./PlanReview";

// ── Language icons ──────────────────────────────────────────────────────────────

function getLanguageIcon(language: string): string {
  const map: Record<string, string> = {
    html: "🌐",
    javascript: "📜",
    js: "📜",
    typescript: "📘",
    ts: "📘",
    tsx: "📘",
    jsx: "📜",
    python: "🐍",
    py: "🐍",
    markdown: "📝",
    md: "📝",
    json: "📋",
    css: "🎨",
    sql: "🗃️",
    bash: "💻",
    sh: "💻",
    yaml: "⚙️",
    yml: "⚙️",
    go: "🔷",
    rust: "🦀",
    ruby: "💎",
    java: "☕",
    swift: "🍎",
    svg: "🖼️",
    xml: "📄",
    csv: "📊",
    text: "📄",
    txt: "📄",
    log: "📋",
    mermaid: "🧜‍♀️",
    pdf: "📕",
    docx: "📘",
    pptx: "📊",
    xlsx: "📗",
  };
  return map[language.toLowerCase()] || "📄";
}

function getLanguageLabel(lang: string): string {
  const labels: Record<string, string> = {
    html: "HTML",
    javascript: "JavaScript",
    js: "JavaScript",
    typescript: "TypeScript",
    ts: "TypeScript",
    tsx: "TSX",
    jsx: "JSX",
    python: "Python",
    py: "Python",
    markdown: "Markdown",
    md: "Markdown",
    json: "JSON",
    css: "CSS",
    sql: "SQL",
    bash: "Bash",
    sh: "Shell",
    yaml: "YAML",
    yml: "YAML",
    go: "Go",
    rust: "Rust",
    java: "Java",
    ruby: "Ruby",
    swift: "Swift",
    kotlin: "Kotlin",
    dart: "Dart",
    xml: "XML",
    svg: "SVG",
    csv: "CSV",
    toml: "TOML",
    text: "Text",
    txt: "Text",
    log: "Log",
    mermaid: "Mermaid",
    pdf: "PDF",
    docx: "Word",
    pptx: "PowerPoint",
    xlsx: "Excel",
  };
  return labels[lang.toLowerCase()] || lang.toUpperCase();
}

/** Format bytes as human-readable size */
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── ArtifactCard ────────────────────────────────────────────────────────────────

interface ArtifactCardProps {
  artifact: Artifact;
  isActive?: boolean;
  onClick: () => void;
  /** Review state when the artifact is a plan */
  planStatus?: PlanStatus;
}

const DESIGN_LANGUAGES = new Set(["html", "svg"]);

/** A design renders as a live thumbnail, like Claude's artifact cards. Scripts
 * run, but without same-origin access the page can't reach the dashboard. */
function DesignThumbnail({ artifact }: { artifact: Artifact }) {
  const srcDoc = artifact.language.toLowerCase() === "svg"
    ? `<!doctype html><html><body style="margin:0;display:grid;place-items:center;min-height:100vh">${artifact.content}</body></html>`
    : artifact.content;
  return (
    <div className="relative h-40 w-full overflow-hidden border-b border-border bg-white" aria-hidden>
      {/* Render at desktop width, then scale down so the layout matches the panel. */}
      <iframe
        srcDoc={srcDoc}
        sandbox="allow-scripts"
        tabIndex={-1}
        title={`${artifact.title} preview`}
        className="pointer-events-none absolute left-0 top-0 h-[320px] w-[640px] origin-top-left scale-50 border-0"
      />
    </div>
  );
}

export default function ArtifactCard({ artifact, isActive, onClick, planStatus }: ArtifactCardProps) {
  const isFileArtifact = !!artifact.file_url;
  const isDesign = !isFileArtifact && DESIGN_LANGUAGES.has(artifact.language.toLowerCase());
  const lineCount = artifact.content ? artifact.content.split("\n").length : 0;
  const charCount = artifact.content ? artifact.content.length : 0;

  const sizeLabel = isFileArtifact && artifact.file_size
    ? formatFileSize(artifact.file_size)
    : charCount > 10000
      ? `${(charCount / 1000).toFixed(0)}K chars`
      : charCount > 1000
        ? `${(charCount / 1000).toFixed(1)}K chars`
        : charCount > 0
          ? `${charCount} chars`
          : null;

  if (artifact.language === "plan") {
    const status = planStatus ?? "pending";
    // The first heading names the plan; the title is the generic fallback.
    const heading = artifact.content.match(/^#+\s+(.+)$/m)?.[1]?.trim();
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "group flex w-full max-w-[420px] items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors",
          isActive ? "border-accent-300 bg-accent-50 shadow-sm" : "border-border bg-muted/50 hover:border-muted-foreground/30 hover:bg-muted",
          status === "superseded" && "opacity-60",
        )}
      >
        <RiListCheck3 size={18} className="shrink-0 text-muted-foreground" />
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-[13px] font-medium text-foreground">{heading || artifact.title}</span>
          <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <PlanStatusDot status={status} />
            {status === "pending" ? "Awaiting your review" : status === "approved" ? "Approved" : "Superseded"} · v{artifact.version}
          </span>
        </span>
        {status === "pending" && <span className="shrink-0 text-[12px] font-medium text-foreground/80">Review</span>}
        <RiArrowRightSLine size={16} className="shrink-0 text-muted-foreground/40 transition-transform group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
      </button>
    );
  }

  if (isDesign) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "group w-full max-w-[320px] overflow-hidden rounded-xl border text-left transition-colors",
          isActive ? "border-accent-300 shadow-sm" : "border-border hover:border-muted-foreground/30",
        )}
      >
        <DesignThumbnail artifact={artifact} />
        <span className="flex items-center gap-2 bg-muted/50 px-3 py-2">
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-foreground/80">{artifact.title}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground">{getLanguageLabel(artifact.language)}</span>
          <RiArrowRightSLine size={16} className="shrink-0 text-muted-foreground/40 transition-transform group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
        </span>
      </button>
    );
  }

  return (
    <Button
      variant="outline"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-2.5 w-full max-w-[320px] px-3 py-2 h-auto rounded-xl text-left transition-all duration-150 group",
        isActive
          ? "bg-accent-50 border-accent-300 shadow-sm"
          : "bg-muted/50 border-border hover:border-muted-foreground/30 hover:bg-muted hover:shadow-sm"
      )}
    >
      {/* Icon */}
      <span className="text-base flex-shrink-0" aria-hidden>
        {getLanguageIcon(artifact.language)}
      </span>

      {/* Info */}
      <div className="flex flex-col min-w-0 flex-1">
        <span className={cn("text-[13px] font-medium truncate", isActive ? "text-foreground" : "text-foreground/80")}>
          {artifact.title}
        </span>
        <span className="text-[11px] text-muted-foreground flex items-center gap-1.5">
          <span>{getLanguageLabel(artifact.language)}</span>
          {lineCount > 0 && (
            <>
              <span className="inline-block w-0.5 h-0.5 rounded-full bg-muted-foreground/40" />
              <span>{lineCount} lines</span>
            </>
          )}
          {sizeLabel && (
            <>
              <span className="inline-block w-0.5 h-0.5 rounded-full bg-muted-foreground/40" />
              <span>{sizeLabel}</span>
            </>
          )}
        </span>
      </div>

      {/* Arrow */}
      <RiArrowRightSLine
        size={16}
        className={cn(
          "flex-shrink-0 transition-transform",
          isActive ? "text-accent-500" : "text-muted-foreground/40 group-hover:text-muted-foreground group-hover:translate-x-0.5"
        )}
      />
    </Button>
  );
}
