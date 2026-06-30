"use client";

import { RiArrowRightSLine } from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Artifact } from "./ArtifactViewer";

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
}

export default function ArtifactCard({ artifact, isActive, onClick }: ArtifactCardProps) {
  const isFileArtifact = !!artifact.file_url;
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
