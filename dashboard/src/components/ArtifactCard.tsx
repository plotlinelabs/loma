"use client";

import type { Artifact } from "./ArtifactViewer";

// \u2500\u2500 Language icons \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

function getLanguageIcon(language: string): string {
  const map: Record<string, string> = {
    html: "\ud83c\udf10",
    javascript: "\ud83d\udcdc",
    js: "\ud83d\udcdc",
    typescript: "\ud83d\udcd8",
    ts: "\ud83d\udcd8",
    tsx: "\ud83d\udcd8",
    jsx: "\ud83d\udcdc",
    python: "\ud83d\udc0d",
    py: "\ud83d\udc0d",
    markdown: "\ud83d\udcdd",
    md: "\ud83d\udcdd",
    json: "\ud83d\udccb",
    css: "\ud83c\udfa8",
    sql: "\ud83d\uddc3\ufe0f",
    bash: "\ud83d\udcbb",
    sh: "\ud83d\udcbb",
    yaml: "\u2699\ufe0f",
    yml: "\u2699\ufe0f",
    go: "\ud83d\udd37",
    rust: "\ud83e\udd80",
    ruby: "\ud83d\udc8e",
    java: "\u2615",
    swift: "\ud83c\udf4e",
    svg: "\ud83d\uddbc\ufe0f",
    xml: "\ud83d\udcc4",
    csv: "\ud83d\udcca",
    text: "\ud83d\udcc4",
    txt: "\ud83d\udcc4",
    log: "\ud83d\udccb",
    mermaid: "\ud83e\udddc\u200d\u2640\ufe0f",
    pdf: "\ud83d\udcd5",
    docx: "\ud83d\udcd8",
    pptx: "\ud83d\udcca",
    xlsx: "\ud83d\udcd7",
  };
  return map[language.toLowerCase()] || "\ud83d\udcc4";
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

// \u2500\u2500 ArtifactCard \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

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
    <button
      type="button"
      onClick={onClick}
      className={`
        inline-flex items-center gap-2.5 w-full max-w-[320px]
        px-3 py-2 rounded-xl border text-left
        transition-all duration-150 group
        ${isActive
          ? "bg-accent-50 border-accent-300 shadow-sm"
          : "bg-gray-50 border-gray-200 hover:border-gray-300 hover:bg-gray-100 hover:shadow-sm"
        }
      `}
    >
      {/* Icon */}
      <span className="text-base flex-shrink-0" aria-hidden>
        {getLanguageIcon(artifact.language)}
      </span>

      {/* Info */}
      <div className="flex flex-col min-w-0 flex-1">
        <span className={`text-sm font-medium truncate ${isActive ? "text-gray-900" : "text-gray-700"}`}>
          {artifact.title}
        </span>
        <span className="text-[11px] text-gray-400 flex items-center gap-1.5">
          <span>{getLanguageLabel(artifact.language)}</span>
          {lineCount > 0 && (
            <>
              <span className="inline-block w-0.5 h-0.5 rounded-full bg-gray-300" />
              <span>{lineCount} lines</span>
            </>
          )}
          {sizeLabel && (
            <>
              <span className="inline-block w-0.5 h-0.5 rounded-full bg-gray-300" />
              <span>{sizeLabel}</span>
            </>
          )}
        </span>
      </div>

      {/* Arrow */}
      <svg
        className={`w-4 h-4 flex-shrink-0 transition-transform ${
          isActive ? "text-accent-500" : "text-gray-300 group-hover:text-gray-400 group-hover:translate-x-0.5"
        }`}
        fill="none"
        viewBox="0 0 24 24"
        strokeWidth={2}
        stroke="currentColor"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
      </svg>
    </button>
  );
}
