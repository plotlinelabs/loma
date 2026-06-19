"use client";

import { useState, useCallback, useRef, useEffect, useMemo } from "react";
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

const PREVIEWABLE_LANGUAGES = new Set(["html", "markdown", "md", "svg", "mermaid", "jsx", "tsx", "pdf", "docx", "xlsx", "pptx"]);
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

// ── Toolbar Icons ───────────────────────────────────────────────────────────

function CopyIcon({ className }: { className?: string }) {
  return (
    <svg className={className || "w-4 h-4"} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.666 3.888A2.25 2.25 0 0 0 13.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 0 1-.75.75H9.75a.75.75 0 0 1-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 0 1-2.25 2.25H6.75A2.25 2.25 0 0 1 4.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 0 1 1.927-.184" />
    </svg>
  );
}

function DownloadIcon({ className }: { className?: string }) {
  return (
    <svg className={className || "w-4 h-4"} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
    </svg>
  );
}

function CloseIcon({ className }: { className?: string }) {
  return (
    <svg className={className || "w-4 h-4"} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
    </svg>
  );
}

function CodeIcon({ className }: { className?: string }) {
  return (
    <svg className={className || "w-4 h-4"} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 6.75 22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3-4.5 16.5" />
    </svg>
  );
}

function EyeIcon({ className }: { className?: string }) {
  return (
    <svg className={className || "w-4 h-4"} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
    </svg>
  );
}

function ExpandIcon({ className }: { className?: string }) {
  return (
    <svg className={className || "w-4 h-4"} fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" />
    </svg>
  );
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
    <div className="p-4 md:p-6 overflow-y-auto h-full">
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
      <pre className="p-4 text-xs font-mono leading-relaxed">
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
        <div className="flex items-center gap-2 text-gray-400 text-sm">
          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Rendering diagram...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 h-full overflow-auto bg-white rounded-b-lg">
        <div className="rounded-lg border border-red-200 bg-red-50 p-4">
          <div className="flex items-center gap-2 text-red-700 text-sm font-medium mb-2">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
            </svg>
            Mermaid Syntax Error
          </div>
          <pre className="text-xs text-red-600 whitespace-pre-wrap font-mono">{error}</pre>
        </div>
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
        <div className="px-3 py-2 bg-red-50 border-b border-red-200 text-xs text-red-600 font-mono truncate">
          {error}
        </div>
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
        <div className="flex items-center gap-2 text-gray-400 text-sm">
          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Converting document...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 h-full overflow-auto bg-white rounded-b-lg">
        <div className="rounded-lg border border-red-200 bg-red-50 p-4">
          <div className="flex items-center gap-2 text-red-700 text-sm font-medium mb-2">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
            </svg>
            Document Error
          </div>
          <pre className="text-xs text-red-600 whitespace-pre-wrap font-mono">{error}</pre>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 h-full overflow-auto bg-white rounded-b-lg">
      <div className="prose prose-sm max-w-none" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

// ── Spreadsheet Renderer (SheetJS/xlsx) ─────────────────────────────────────

function SpreadsheetRenderer({ fileUrl }: { fileUrl: string }) {
  const [sheets, setSheets] = useState<{ name: string; html: string }[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);
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
        const XLSX = await import("xlsx");
        const workbook = XLSX.read(arrayBuffer, { type: "array" });
        const sheetData = workbook.SheetNames.map((name: string) => ({
          name,
          html: XLSX.utils.sheet_to_html(workbook.Sheets[name]),
        }));
        if (!cancelled) {
          setSheets(sheetData);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to render spreadsheet");
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
        <div className="flex items-center gap-2 text-gray-400 text-sm">
          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Loading spreadsheet...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 h-full overflow-auto bg-white rounded-b-lg">
        <div className="rounded-lg border border-red-200 bg-red-50 p-4">
          <div className="text-red-700 text-sm font-medium mb-2">Spreadsheet Error</div>
          <pre className="text-xs text-red-600 whitespace-pre-wrap font-mono">{error}</pre>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-white rounded-b-lg">
      {sheets.length > 1 && (
        <div className="flex gap-1 px-3 pt-2 border-b border-gray-200 overflow-x-auto flex-shrink-0">
          {sheets.map((sheet, i) => (
            <button
              key={sheet.name}
              onClick={() => setActiveSheet(i)}
              className={`px-3 py-1.5 text-xs font-medium rounded-t-lg border border-b-0 transition-colors ${
                i === activeSheet
                  ? "bg-white border-gray-200 text-gray-800"
                  : "bg-gray-50 border-transparent text-gray-500 hover:text-gray-700"
              }`}
            >
              {sheet.name}
            </button>
          ))}
        </div>
      )}
      <div className="flex-1 overflow-auto p-2">
        <div
          className="spreadsheet-preview [&_table]:w-full [&_table]:border-collapse [&_td]:border [&_td]:border-gray-200 [&_td]:px-2 [&_td]:py-1 [&_td]:text-xs [&_th]:border [&_th]:border-gray-200 [&_th]:px-2 [&_th]:py-1 [&_th]:text-xs [&_th]:font-medium [&_th]:bg-gray-50"
          dangerouslySetInnerHTML={{ __html: sheets[activeSheet]?.html || "" }}
        />
      </div>
    </div>
  );
}

// ── PPTX Renderer (download prompt) ─────────────────────────────────────────

function PptxRenderer({ fileUrl, title }: { fileUrl: string; title: string }) {
  return (
    <div className="flex flex-col items-center justify-center h-full bg-white rounded-b-lg gap-4 p-8">
      <div className="w-16 h-16 bg-orange-50 rounded-2xl flex items-center justify-center">
        <span className="text-3xl">📊</span>
      </div>
      <div className="text-center">
        <h3 className="text-lg font-medium text-gray-800 mb-1">{title}</h3>
        <p className="text-sm text-gray-500">PowerPoint files cannot be previewed in the browser.</p>
      </div>
      <a
        href={fileUrl}
        download
        className="inline-flex items-center gap-2 px-4 py-2 bg-accent-200 hover:bg-accent-300 text-accent-on rounded-lg text-sm font-medium transition-colors"
      >
        <DownloadIcon className="w-4 h-4" />
        Download File
      </a>
    </div>
  );
}

// ── Generic File Download Renderer (fallback) ───────────────────────────────

function FileDownloadRenderer({ fileUrl, title, fileSize }: { fileUrl: string; title: string; fileSize?: number }) {
  return (
    <div className="flex flex-col items-center justify-center h-full bg-white rounded-b-lg gap-4 p-8">
      <div className="w-16 h-16 bg-gray-100 rounded-2xl flex items-center justify-center">
        <span className="text-3xl">📄</span>
      </div>
      <div className="text-center">
        <h3 className="text-lg font-medium text-gray-800 mb-1">{title}</h3>
        {fileSize != null && (
          <p className="text-sm text-gray-500">{formatFileSize(fileSize)}</p>
        )}
      </div>
      <a
        href={fileUrl}
        download
        className="inline-flex items-center gap-2 px-4 py-2 bg-accent-200 hover:bg-accent-300 text-accent-on rounded-lg text-sm font-medium transition-colors"
      >
        <DownloadIcon className="w-4 h-4" />
        Download File
      </a>
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
    ? "fixed inset-0 z-[90] bg-surface flex flex-col animate-fade-in"
    : "flex flex-col h-full bg-surface border-l border-gray-200 animate-artifact-slide-in";

  return (
    <div className={containerClass}>
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 bg-gray-50 flex-shrink-0">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          {/* Language badge */}
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-gray-200 text-gray-600 flex-shrink-0">
            {getLanguageLabel(artifact.language)}
          </span>
          {/* Title */}
          <span className="text-sm font-medium text-gray-800 truncate" title={artifact.title}>
            {artifact.title}
          </span>
          {/* File size badge (for file artifacts) */}
          {isFileArtifact && artifact.file_size != null && (
            <span className="text-[10px] text-gray-400 flex-shrink-0">
              {formatFileSize(artifact.file_size)}
            </span>
          )}
          {/* Version indicator */}
          {versions.length > 1 && (
            <span className="text-[10px] text-gray-400 flex-shrink-0">
              v{artifact.version}{versions.length > 1 ? ` of ${versions.length}` : ""}
            </span>
          )}
        </div>

        <div className="flex items-center gap-0.5 flex-shrink-0">
          {/* Preview/Code toggle — only show for text-based artifacts with preview */}
          {canPreview && !isFileArtifact && (
            <div className="flex items-center bg-gray-200 rounded-lg p-0.5 mr-1">
              <button
                onClick={() => setViewMode("preview")}
                className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium transition-colors ${
                  viewMode === "preview"
                    ? "bg-surface text-gray-800 shadow-sm"
                    : "text-gray-500 hover:text-gray-700"
                }`}
                title="Preview"
              >
                <EyeIcon className="w-3.5 h-3.5" />
                Preview
              </button>
              <button
                onClick={() => setViewMode("code")}
                className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs font-medium transition-colors ${
                  viewMode === "code"
                    ? "bg-surface text-gray-800 shadow-sm"
                    : "text-gray-500 hover:text-gray-700"
                }`}
                title="Code"
              >
                <CodeIcon className="w-3.5 h-3.5" />
                Code
              </button>
            </div>
          )}

          {/* Version selector */}
          {versions.length > 1 && (
            <div className="flex items-center gap-0.5 mr-1">
              <button
                onClick={() => {
                  const idx = versions.findIndex((v) => v.id === artifact.id);
                  if (idx > 0 && onSelectArtifact) onSelectArtifact(versions[idx - 1].id);
                }}
                disabled={versions.findIndex((v) => v.id === artifact.id) === 0}
                className="p-1 text-gray-400 hover:text-gray-600 disabled:opacity-30 rounded transition-colors"
                title="Previous version"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
                </svg>
              </button>
              <button
                onClick={() => {
                  const idx = versions.findIndex((v) => v.id === artifact.id);
                  if (idx < versions.length - 1 && onSelectArtifact) onSelectArtifact(versions[idx + 1].id);
                }}
                disabled={versions.findIndex((v) => v.id === artifact.id) === versions.length - 1}
                className="p-1 text-gray-400 hover:text-gray-600 disabled:opacity-30 rounded transition-colors"
                title="Next version"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" />
                </svg>
              </button>
            </div>
          )}

          {/* Copy — only for text-based artifacts */}
          {!isFileArtifact && (
            <button
              onClick={handleCopy}
              className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
              title={copyFeedback ? "Copied!" : "Copy to clipboard"}
            >
              {copyFeedback ? (
                <svg className="w-4 h-4 text-green-500" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              ) : (
                <CopyIcon />
              )}
            </button>
          )}

          {/* Download */}
          <button
            onClick={handleDownload}
            className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
            title="Download file"
          >
            <DownloadIcon />
          </button>

          {/* Fullscreen toggle */}
          <button
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
            title={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
          >
            <ExpandIcon />
          </button>

          {/* Close */}
          <button
            onClick={() => {
              if (isFullscreen) setIsFullscreen(false);
              onClose();
            }}
            className="p-1.5 text-gray-400 hover:text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
            title="Close (Esc)"
          >
            <CloseIcon />
          </button>
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
          ) : artifact.file_url && artifact.language === "xlsx" ? (
            <SpreadsheetRenderer fileUrl={artifact.file_url} />
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
