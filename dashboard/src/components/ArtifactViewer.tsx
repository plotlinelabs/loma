"use client";

import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import {
  RiFileCopyLine,
  RiDownloadLine,
  RiCloseLine,
  RiCodeSLine,
  RiEyeLine,
  RiFullscreenLine,
  RiArrowLeftSLine,
  RiArrowRightSLine,
  RiCheckLine,
  RiLoader4Line,
  RiErrorWarningLine,
} from "@remixicon/react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { cn } from "@/lib/utils";
import MarkdownContent from "./MarkdownContent";

// ── Types ───────────────────────────────────────────────────────────────────

export interface Artifact {
  id: string;
  title: string;
  content: string;
  language: string; // e.g. "html", "javascript", "markdown", "python", "json", "mermaid", etc.
  version: number;
  timestamp: number;
  // File artifact fields (optional — only present for file artifacts)
  file_url?: string;
  file_size?: number;
  file_type?: string;
  previews?: string[];
}

type ViewMode = "preview" | "code";

// ── Language detection ──────────────────────────────────────────────────────

const PREVIEWABLE_LANGUAGES = new Set(["html", "markdown", "md", "svg", "mermaid", "jsx", "tsx", "pdf", "docx", "pptx"]);
const CODE_LANGUAGES = new Set([
  "javascript", "js", "typescript", "ts", "tsx", "jsx",
  "python", "py", "java", "go", "rust", "ruby", "rb",
  "c", "cpp", "csharp", "cs", "swift", "kotlin",
  "php", "sql", "bash", "sh", "shell", "zsh",
  "yaml", "yml", "toml", "ini", "cfg",
  "json", "xml", "css", "scss", "less",
  "dockerfile", "makefile", "graphql", "protobuf",
  "dart", "lua", "r", "scala", "perl", "haskell",
  "elixir", "erlang", "clojure", "ocaml",
  "text", "txt", "log", "csv",
  "mermaid",
]);

function isPreviewable(language: string): boolean {
  return PREVIEWABLE_LANGUAGES.has(language.toLowerCase());
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
    dockerfile: "Dockerfile",
    graphql: "GraphQL",
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

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ── HTML Renderer (sandboxed iframe) ────────────────────────────────────────

function HtmlRenderer({ content }: { content: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    if (!iframeRef.current) return;
    const doc = iframeRef.current.contentDocument;
    if (!doc) return;

    // Write content to sandboxed iframe
    doc.open();
    doc.write(content);
    doc.close();
  }, [content]);

  return (
    <iframe
      ref={iframeRef}
      sandbox="allow-scripts allow-same-origin"
      className="w-full h-full border-0 bg-white rounded-b-lg"
      title="HTML Preview"
    />
  );
}

// ── Markdown Renderer ───────────────────────────────────────────────────────

function MarkdownRenderer({ content }: { content: string }) {
  return (
    <div className="p-3 md:p-6 overflow-y-auto h-full">
      <div className="prose prose-sm max-w-none">
        <MarkdownContent content={content} />
      </div>
    </div>
  );
}

// ── Code Renderer (with line numbers) ───────────────────────────────────────

function CodeRenderer({ content, language }: { content: string; language: string }) {
  const lines = useMemo(() => content.split("\n"), [content]);

  return (
    <div className="h-full overflow-auto bg-[#0A0A0A] rounded-b-lg">
      {language && (
        <div className="sticky top-0 z-10 text-[10px] font-mono text-[#8C857D] bg-[#0A0A0A] px-4 py-1.5 border-b border-[#1F1D1A]">
          {getLanguageLabel(language)}
        </div>
      )}
      <pre className="p-3 text-xs font-mono leading-relaxed">
        <code>
          {lines.map((line, i) => (
            <div key={i} className="flex">
              <span className="select-none text-[#5C5650] w-8 text-right mr-4 flex-shrink-0">
                {i + 1}
              </span>
              <span className="text-[#E8E4DD] flex-1 whitespace-pre-wrap break-all">
                {line || " "}
              </span>
            </div>
          ))}
        </code>
      </pre>
    </div>
  );
}

// ── SVG Renderer ────────────────────────────────────────────────────────────

function SvgRenderer({ content }: { content: string }) {
  return (
    <div
      className="p-6 h-full overflow-auto flex items-center justify-center bg-white dark:bg-gray-800 rounded-b-lg"
      dangerouslySetInnerHTML={{ __html: content }}
    />
  );
}

// ── Mermaid Renderer ────────────────────────────────────────────────────────

function MermaidRenderer({ content }: { content: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [svgOutput, setSvgOutput] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function renderMermaid() {
      setLoading(true);
      setError(null);
      setSvgOutput("");

      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: "default",
          securityLevel: "loose",
          fontFamily: "ui-sans-serif, system-ui, sans-serif",
        });

        const id = `mermaid-${Date.now()}`;
        const { svg } = await mermaid.render(id, content);

        if (!cancelled) {
          setSvgOutput(svg);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to render Mermaid diagram");
          setLoading(false);
        }
      }
    }

    renderMermaid();
    return () => { cancelled = true; };
  }, [content]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-white rounded-b-lg">
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <RiLoader4Line size={16} className="animate-spin" />
          Rendering diagram...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-3 h-full overflow-auto bg-white rounded-b-lg">
        <Alert variant="destructive">
          <RiErrorWarningLine size={16} />
          <AlertDescription>
            <div className="font-medium mb-2">Mermaid Syntax Error</div>
            <pre className="text-xs whitespace-pre-wrap font-mono">{error}</pre>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="p-6 h-full overflow-auto flex items-center justify-center bg-white rounded-b-lg"
      dangerouslySetInnerHTML={{ __html: svgOutput }}
    />
  );
}

// ── React/JSX Live Preview (sandboxed iframe) ───────────────────────────────

function ReactRenderer({ content }: { content: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);

    // Listen for error messages from the iframe
    function handleMessage(event: MessageEvent) {
      if (event.data?.type === "react-preview-error") {
        setError(event.data.message);
      }
    }
    window.addEventListener("message", handleMessage);

    if (!iframeRef.current) return;
    const doc = iframeRef.current.contentDocument;
    if (!doc) return;

    // Escape closing script tags inside the content to prevent breaking the HTML
    const escapedContent = content.replace(/<\/script>/gi, "<\\/script>");

    // Build a self-contained HTML page with React 18, Babel, and Tailwind CSS
    const htmlContent = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <script src="https://unpkg.com/react@18/umd/react.development.js" crossorigin></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js" crossorigin></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <link href="https://cdn.jsdelivr.net/npm/tailwindcss@2/dist/tailwind.min.css" rel="stylesheet" />
  <style>
    body { margin: 0; padding: 16px; font-family: ui-sans-serif, system-ui, sans-serif; }
    #root { min-height: 100%; }
    .error-display { color: #dc2626; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 16px; font-family: monospace; font-size: 12px; white-space: pre-wrap; }
  </style>
</head>
<body>
  <div id="root"></div>
  <script type="text/babel" data-type="module">
    try {
      const { useState, useEffect, useRef, useMemo, useCallback, useReducer, useContext, createContext, Fragment } = React;

      ${escapedContent}

      // Try to find the default export or a component named App
      const ComponentToRender = typeof App !== 'undefined' ? App
        : typeof Component !== 'undefined' ? Component
        : typeof Default !== 'undefined' ? Default
        : null;

      if (ComponentToRender) {
        const root = ReactDOM.createRoot(document.getElementById('root'));
        root.render(React.createElement(ComponentToRender));
      } else {
        document.getElementById('root').innerHTML = '<div class="error-display">No component found to render. Export a component named App, Component, or Default.</div>';
      }
    } catch (err) {
      document.getElementById('root').innerHTML = '<div class="error-display">' + err.message + '</div>';
      window.parent.postMessage({ type: 'react-preview-error', message: err.message }, '*');
    }
  </script>
</body>
</html>`;

    doc.open();
    doc.write(htmlContent);
    doc.close();

    return () => {
      window.removeEventListener("message", handleMessage);
    };
  }, [content]);

  return (
    <div className="h-full flex flex-col rounded-b-lg overflow-hidden">
      {error && (
        <Alert variant="destructive" className="rounded-none border-x-0 border-t-0 text-xs font-mono">
          <AlertDescription className="truncate">{error}</AlertDescription>
        </Alert>
      )}
      <iframe
        ref={iframeRef}
        sandbox="allow-scripts allow-same-origin"
        className="w-full flex-1 border-0 bg-white"
        title="React Preview"
      />
    </div>
  );
}

// ── PDF Renderer (iframe) ───────────────────────────────────────────────────

function PdfRenderer({ fileUrl }: { fileUrl: string }) {
  return (
    <iframe
      src={fileUrl}
      className="w-full h-full border-0 bg-white rounded-b-lg"
      title="PDF Preview"
    />
  );
}

// ── DOCX Renderer (mammoth.js) ──────────────────────────────────────────────

function DocxRenderer({ fileUrl }: { fileUrl: string }) {
  const [html, setHtml] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function convert() {
      setLoading(true);
      setError(null);
      try {
        const response = await fetch(fileUrl);
        if (!response.ok) throw new Error(`Failed to fetch file: ${response.status}`);
        const arrayBuffer = await response.arrayBuffer();
        const mammoth = await import("mammoth");
        const result = await mammoth.convertToHtml({ arrayBuffer });
        if (!cancelled) {
          setHtml(result.value);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to render document");
          setLoading(false);
        }
      }
    }
    convert();
    return () => { cancelled = true; };
  }, [fileUrl]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-white rounded-b-lg">
        <div className="flex items-center gap-2 text-muted-foreground text-sm">
          <RiLoader4Line size={16} className="animate-spin" />
          Converting document...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-3 h-full overflow-auto bg-white rounded-b-lg">
        <Alert variant="destructive">
          <RiErrorWarningLine size={16} />
          <AlertDescription>
            <div className="font-medium mb-2">Document Error</div>
            <pre className="text-xs whitespace-pre-wrap font-mono">{error}</pre>
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  return (
    <div className="p-6 h-full overflow-auto bg-white rounded-b-lg">
      <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

// ── PPTX Renderer (download prompt) ─────────────────────────────────────────

function PptxRenderer({ fileUrl, title }: { fileUrl: string; title: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-full bg-white rounded-b-lg gap-3 p-6">
      <div className="w-16 h-16 bg-orange-50 rounded-2xl flex items-center justify-center">
        <span className="text-3xl">📊</span>
      </div>
      <div className="text-center">
        <h3 className="text-lg font-medium text-foreground mb-1">{title}</h3>
        <p className="text-sm text-muted-foreground">PowerPoint files cannot be previewed in the browser.</p>
      </div>
      <Button asChild variant="default" className="bg-accent-200 hover:bg-accent-300 text-accent-on">
        <a href={fileUrl} download>
          <RiDownloadLine size={16} />
          Download File
        </a>
      </Button>
    </div>
  );
}

// ── Generic File Download Renderer (fallback) ───────────────────────────────

function FileDownloadRenderer({ fileUrl, title, fileSize }: { fileUrl: string; title: string; fileSize?: number }) {
  return (
    <div className="flex flex-col items-center justify-center h-full bg-white rounded-b-lg gap-3 p-6">
      <div className="w-16 h-16 bg-muted rounded-2xl flex items-center justify-center">
        <span className="text-3xl">📄</span>
      </div>
      <div className="text-center">
        <h3 className="text-lg font-medium text-foreground mb-1">{title}</h3>
        {fileSize != null && (
          <p className="text-sm text-muted-foreground">{formatFileSize(fileSize)}</p>
        )}
      </div>
      <Button asChild variant="default" className="bg-accent-200 hover:bg-accent-300 text-accent-on">
        <a href={fileUrl} download>
          <RiDownloadLine size={16} />
          Download File
        </a>
      </Button>
    </div>
  );
}

// ── Main ArtifactViewer ─────────────────────────────────────────────────────

interface ArtifactViewerProps {
  artifact: Artifact;
  onClose: () => void;
  /** All artifacts for version navigation */
  allArtifacts?: Artifact[];
  /** Navigate to a specific artifact version */
  onSelectArtifact?: (id: string) => void;
}

export default function ArtifactViewer({
  artifact,
  onClose,
  allArtifacts,
  onSelectArtifact,
}: ArtifactViewerProps) {
  const canPreview = isPreviewable(artifact.language) || !!artifact.file_url;
  const isFileArtifact = !!artifact.file_url;
  const [viewMode, setViewMode] = useState<ViewMode>(canPreview ? "preview" : "code");
  const [copyFeedback, setCopyFeedback] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Reset view mode when artifact changes
  useEffect(() => {
    const newCanPreview = isPreviewable(artifact.language) || !!artifact.file_url;
    setViewMode(newCanPreview ? "preview" : "code");
  }, [artifact.id, artifact.language, artifact.file_url]);

  // Keyboard shortcut: Escape to close
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (isFullscreen) {
          setIsFullscreen(false);
        } else {
          onClose();
        }
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [onClose, isFullscreen]);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(artifact.content);
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 2000);
    } catch {
      // Fallback for non-secure contexts
      const textarea = document.createElement("textarea");
      textarea.value = artifact.content;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      setCopyFeedback(true);
      setTimeout(() => setCopyFeedback(false), 2000);
    }
  }, [artifact.content]);

  const handleDownload = useCallback(() => {
    if (artifact.file_url) {
      // File artifact: download from file_url
      const a = document.createElement("a");
      a.href = artifact.file_url;
      a.download = artifact.title;
      a.click();
    } else {
      // Text artifact: create blob from content
      const ext = getFileExtension(artifact.language);
      const filename = `${artifact.title.replace(/[^a-zA-Z0-9_-]/g, "_")}.${ext}`;
      const blob = new Blob([artifact.content], { type: getMimeType(artifact.language) });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    }
  }, [artifact]);

  // Get versions of this artifact (by title match)
  const versions = useMemo(() => {
    if (!allArtifacts) return [];
    return allArtifacts
      .filter((a) => a.title === artifact.title)
      .sort((a, b) => a.version - b.version);
  }, [allArtifacts, artifact.title]);

  const containerClass = isFullscreen
    ? "fixed inset-0 z-[90] bg-card flex flex-col animate-fade-in"
    : "flex flex-col h-full bg-card border-l border-border animate-artifact-slide-in";

  return (
    <div className={containerClass}>
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-muted/50 flex-shrink-0">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {/* Language badge */}
          <Badge variant="secondary" className="text-[10px] font-mono flex-shrink-0">
            {getLanguageLabel(artifact.language)}
          </Badge>
          {/* Title */}
          <span className="text-sm font-medium text-foreground truncate" title={artifact.title}>
            {artifact.title}
          </span>
          {/* File size badge (for file artifacts) */}
          {isFileArtifact && artifact.file_size != null && (
            <span className="text-[10px] text-muted-foreground flex-shrink-0">
              {formatFileSize(artifact.file_size)}
            </span>
          )}
          {/* Version indicator */}
          {versions.length > 1 && (
            <span className="text-[10px] text-muted-foreground flex-shrink-0">
              v{artifact.version}{versions.length > 1 ? ` of ${versions.length}` : ""}
            </span>
          )}
        </div>

        <div className="flex items-center gap-0.5 flex-shrink-0">
          {/* Preview/Code toggle — only show for text-based artifacts with preview */}
          {canPreview && !isFileArtifact && (
            <div className="flex items-center bg-muted rounded-lg p-0.5 mr-1">
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setViewMode("preview")}
                className={cn(
                  "flex items-center gap-1 rounded-md text-xs font-medium transition-colors",
                  viewMode === "preview"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
                title="Preview"
              >
                <RiEyeLine size={14} />
                Preview
              </Button>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => setViewMode("code")}
                className={cn(
                  "flex items-center gap-1 rounded-md text-xs font-medium transition-colors",
                  viewMode === "code"
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                )}
                title="Code"
              >
                <RiCodeSLine size={14} />
                Code
              </Button>
            </div>
          )}

          {/* Version selector */}
          {versions.length > 1 && (
            <div className="flex items-center gap-0.5 mr-1">
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => {
                  const idx = versions.findIndex((v) => v.id === artifact.id);
                  if (idx > 0 && onSelectArtifact) onSelectArtifact(versions[idx - 1].id);
                }}
                disabled={versions.findIndex((v) => v.id === artifact.id) === 0}
                className="text-muted-foreground hover:text-foreground"
                title="Previous version"
              >
                <RiArrowLeftSLine size={14} />
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                onClick={() => {
                  const idx = versions.findIndex((v) => v.id === artifact.id);
                  if (idx < versions.length - 1 && onSelectArtifact) onSelectArtifact(versions[idx + 1].id);
                }}
                disabled={versions.findIndex((v) => v.id === artifact.id) === versions.length - 1}
                className="text-muted-foreground hover:text-foreground"
                title="Next version"
              >
                <RiArrowRightSLine size={14} />
              </Button>
            </div>
          )}

          {/* Copy — only for text-based artifacts */}
          {!isFileArtifact && (
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={handleCopy}
              className="text-muted-foreground hover:text-foreground"
              title={copyFeedback ? "Copied!" : "Copy to clipboard"}
            >
              {copyFeedback ? (
                <RiCheckLine size={16} className="text-green-500" />
              ) : (
                <RiFileCopyLine size={16} />
              )}
            </Button>
          )}

          {/* Download */}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={handleDownload}
            className="text-muted-foreground hover:text-foreground"
            title="Download file"
          >
            <RiDownloadLine size={16} />
          </Button>

          {/* Fullscreen toggle */}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="text-muted-foreground hover:text-foreground"
            title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
          >
            <RiFullscreenLine size={16} />
          </Button>

          {/* Close */}
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => {
              if (isFullscreen) setIsFullscreen(false);
              onClose();
            }}
            className="text-muted-foreground hover:text-foreground"
            title="Close (Esc)"
          >
            <RiCloseLine size={16} />
          </Button>
        </div>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-hidden min-h-0">
        {viewMode === "preview" ? (
          // File artifact renderers (file_url-based) — check these first
          artifact.file_url && artifact.language === "pdf" ? (
            <PdfRenderer fileUrl={artifact.file_url} />
          ) : artifact.file_url && artifact.language === "docx" ? (
            <DocxRenderer fileUrl={artifact.file_url} />
          ) : artifact.file_url && artifact.language === "pptx" ? (
            <PptxRenderer fileUrl={artifact.file_url} title={artifact.title} />
          ) : artifact.file_url ? (
            <FileDownloadRenderer fileUrl={artifact.file_url} title={artifact.title} fileSize={artifact.file_size} />
          // Text-based renderers (content-based)
          ) : artifact.language === "html" ? (
            <HtmlRenderer content={artifact.content} />
          ) : artifact.language === "svg" ? (
            <SvgRenderer content={artifact.content} />
          ) : artifact.language === "markdown" || artifact.language === "md" ? (
            <MarkdownRenderer content={artifact.content} />
          ) : artifact.language === "mermaid" ? (
            <MermaidRenderer content={artifact.content} />
          ) : artifact.language === "jsx" || artifact.language === "tsx" ? (
            <ReactRenderer content={artifact.content} />
          ) : null
        ) : (
          <CodeRenderer content={artifact.content} language={artifact.language} />
        )}
      </div>
    </div>
  );
}

// ── Utility functions ───────────────────────────────────────────────────────

function getFileExtension(language: string): string {
  const map: Record<string, string> = {
    javascript: "js",
    typescript: "ts",
    python: "py",
    markdown: "md",
    md: "md",
    html: "html",
    css: "css",
    json: "json",
    yaml: "yml",
    yml: "yml",
    bash: "sh",
    sh: "sh",
    sql: "sql",
    xml: "xml",
    svg: "svg",
    go: "go",
    rust: "rs",
    ruby: "rb",
    java: "java",
    swift: "swift",
    kotlin: "kt",
    dart: "dart",
    text: "txt",
    txt: "txt",
    log: "log",
    csv: "csv",
    toml: "toml",
    tsx: "tsx",
    jsx: "jsx",
    mermaid: "mmd",
    pdf: "pdf",
    docx: "docx",
    pptx: "pptx",
    xlsx: "xlsx",
  };
  return map[language.toLowerCase()] || language.toLowerCase();
}

function getMimeType(language: string): string {
  const map: Record<string, string> = {
    html: "text/html",
    javascript: "application/javascript",
    js: "application/javascript",
    typescript: "text/typescript",
    ts: "text/typescript",
    json: "application/json",
    css: "text/css",
    markdown: "text/markdown",
    md: "text/markdown",
    xml: "application/xml",
    svg: "image/svg+xml",
    yaml: "text/yaml",
    yml: "text/yaml",
    python: "text/x-python",
    py: "text/x-python",
    sql: "text/x-sql",
    mermaid: "text/x-mermaid",
    jsx: "text/jsx",
    tsx: "text/tsx",
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  };
  return map[language.toLowerCase()] || "text/plain";
}
