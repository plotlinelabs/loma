"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { SkillDetailResponse } from "../../../lib/api";
import { basePath, skillAssetUrl, updateSkillFile } from "../../../lib/api";
import SkillEmptyState from "./SkillEmptyState";

const PROSE_CLASSES =
  "prose prose-sm max-w-none prose-headings:text-gray-900 prose-p:text-gray-700 prose-a:text-brand-600 prose-strong:text-gray-800 prose-code:text-brand-700 prose-code:bg-brand-50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-pre:bg-gray-50 prose-pre:border prose-pre:border-gray-100 prose-li:text-gray-700";

function stripFrontmatter(content: string): string {
  if (!content.startsWith("---")) return content;
  const end = content.indexOf("---", 3);
  if (end === -1) return content;
  return content.slice(end + 3).trimStart();
}

function parseFrontmatter(content: string): Record<string, string> {
  if (!content.startsWith("---")) return {};
  const end = content.indexOf("---", 3);
  if (end === -1) return {};
  const block = content.slice(3, end).trim();
  const fields: Record<string, string> = {};
  for (const line of block.split("\n")) {
    const idx = line.indexOf(":");
    if (idx > 0) {
      const key = line.slice(0, idx).trim();
      const val = line.slice(idx + 1).trim();
      if (key && val) fields[key] = val;
    }
  }
  return fields;
}

function chatUrl(prompt: string): string {
  return `${basePath}/chat?prompt=${encodeURIComponent(prompt)}`;
}

function buildChatPrompt(skill: SkillDetailResponse, filePath: string): string {
  const slug = skill.slug || skill.name;
  return [
    `I want to edit \`${filePath}\` in the Loma skill \`${slug}\`.`,
    "",
    `Skill name: ${skill.name || slug}`,
    `Description: ${skill.description || "No description"}`,
    "",
    `First inspect it with \`python3 tools/loma_skills.py file --slug ${slug} --path ${filePath}\` and fetch the whole skill with \`python3 tools/loma_skills.py dump --slug ${slug}\` if you need broader context.`,
    "",
    "Help me decide the exact change. Only update the live skill after I explicitly confirm what to change.",
  ].join("\n");
}

function Breadcrumb({
  scope,
  skillName,
  filePath,
  onNavigate,
}: {
  scope: string;
  skillName: string;
  filePath: string | null;
  onNavigate: (level: "root" | "skill") => void;
}) {
  const scopeLabel = scope.charAt(0).toUpperCase() + scope.slice(1);
  return (
    <div className="flex items-center gap-1.5 text-xs text-gray-400">
      <button onClick={() => onNavigate("root")} className="hover:text-gray-600 transition-colors">
        Skills
      </button>
      <span>/</span>
      <span>{scopeLabel}</span>
      <span>/</span>
      <button onClick={() => onNavigate("skill")} className="hover:text-gray-600 transition-colors">
        {skillName}
      </button>
      {filePath && (
        <>
          <span>/</span>
          <span className="text-gray-600">{filePath}</span>
        </>
      )}
    </div>
  );
}

export default function SkillDetailPane({
  skill,
  selectedFilePath,
  loading,
  createUrl,
  onNavigate,
  onSkillUpdated,
}: {
  skill: SkillDetailResponse | null;
  selectedFilePath: string | null;
  loading: boolean;
  createUrl: string;
  onNavigate: (level: "root" | "skill") => void;
  onSkillUpdated: () => void;
}) {
  const [mode, setMode] = useState<"viewer" | "editor">("viewer");
  const [editorContent, setEditorContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [chatInput, setChatInput] = useState("");

  const filePath = selectedFilePath || "SKILL.md";
  const isSystemSkill = skill?.scope === "system";
  const isEditable = !isSystemSkill && mode === "editor";

  const fileContent = useMemo(() => {
    if (!skill) return "";
    if (filePath === "SKILL.md") return skill.content;
    return skill.extra_files[filePath] || "";
  }, [skill, filePath]);

  const isMarkdown = filePath.endsWith(".md");
  const isAssetFile = skill?.files.some(
    (f) => f.path === filePath && f.kind === "local_asset"
  );

  const frontmatter = useMemo(() => {
    if (!isMarkdown) return {};
    return parseFrontmatter(fileContent);
  }, [fileContent, isMarkdown]);

  function handleModeSwitch(newMode: "viewer" | "editor") {
    if (newMode === "editor") {
      setEditorContent(fileContent);
    }
    setMode(newMode);
  }

  async function handleSave() {
    if (!skill || saving) return;
    const slug = skill.slug || skill.name;
    setSaving(true);
    try {
      await updateSkillFile(slug, filePath, editorContent);
      onSkillUpdated();
      setMode("viewer");
    } catch {
      // Error handling — could show a toast, for now just let saving state clear
    } finally {
      setSaving(false);
    }
  }

  function handleDownload() {
    const blob = new Blob([mode === "editor" ? editorContent : fileContent], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filePath;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleChatSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!chatInput.trim() || !skill) return;
    const slug = skill.slug || skill.name;
    const prompt = [
      chatInput.trim(),
      "",
      `Context: Loma skill \`${slug}\`, file \`${filePath}\`.`,
      `Inspect with \`python3 tools/loma_skills.py file --slug ${slug} --path ${filePath}\`.`,
    ].join("\n");
    window.location.href = chatUrl(prompt);
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-sm text-gray-400">Loading skill...</div>
      </div>
    );
  }

  if (!skill) {
    return <SkillEmptyState createUrl={createUrl} />;
  }

  const slug = skill.slug || skill.name;

  return (
    <div className="flex-1 flex flex-col min-w-0 h-full">
      {/* Top bar: breadcrumb + actions */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100 flex-shrink-0">
        <Breadcrumb
          scope={skill.scope || "personal"}
          skillName={skill.name || slug}
          filePath={selectedFilePath}
          onNavigate={onNavigate}
        />
        <div className="flex items-center gap-2">
          {mode === "editor" && (
            <button
              onClick={handleDownload}
              className="p-1.5 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
              title="Download"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Tabs: Viewer / Editor */}
      {!isAssetFile && (
        <div className="flex items-center gap-0 px-5 border-b border-gray-100 flex-shrink-0">
          <button
            onClick={() => handleModeSwitch("viewer")}
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
              mode === "viewer"
                ? "border-gray-900 text-gray-900"
                : "border-transparent text-gray-400 hover:text-gray-600"
            }`}
          >
            Viewer
          </button>
          <button
            onClick={() => handleModeSwitch("editor")}
            className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
              mode === "editor"
                ? "border-gray-900 text-gray-900"
                : "border-transparent text-gray-400 hover:text-gray-600"
            }`}
          >
            Editor
          </button>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        {isAssetFile ? (
          <div className="space-y-4">
            {skill.files
              .filter((f) => f.path === filePath && f.kind === "local_asset")
              .map((file) => (
                <div key={file.path}>
                  {file.content_type?.startsWith("image/") && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={skillAssetUrl(slug, file.path)}
                      alt={file.path}
                      className="max-w-full max-h-[520px] rounded-lg border border-gray-200"
                    />
                  )}
                  {file.content_type === "application/pdf" && (
                    <iframe
                      src={skillAssetUrl(slug, file.path)}
                      className="w-full h-[520px] rounded-lg border border-gray-200"
                    />
                  )}
                  {!file.content_type?.startsWith("image/") && file.content_type !== "application/pdf" && (
                    <a
                      href={skillAssetUrl(slug, file.path)}
                      target="_blank"
                      rel="noreferrer"
                      className="text-sm text-brand-700 hover:text-brand-800 font-medium"
                    >
                      Download {file.path}
                    </a>
                  )}
                </div>
              ))}
          </div>
        ) : mode === "viewer" ? (
          <div>
            {/* Frontmatter fields */}
            {isMarkdown && Object.keys(frontmatter).length > 0 && (
              <div className="mb-6 space-y-2">
                {Object.entries(frontmatter).map(([key, value]) => (
                  <div key={key} className="flex gap-3">
                    <span className="text-xs text-gray-400 min-w-[80px] pt-0.5">{key}</span>
                    <span className="text-sm text-gray-700">{value}</span>
                  </div>
                ))}
              </div>
            )}
            {/* Rendered content */}
            {isMarkdown ? (
              <div className={PROSE_CLASSES}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripFrontmatter(fileContent)}</ReactMarkdown>
              </div>
            ) : (
              <pre className="whitespace-pre-wrap text-xs text-gray-700 leading-relaxed bg-gray-50 rounded-lg p-4 border border-gray-100 overflow-x-auto">
                {fileContent}
              </pre>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {isEditable && (
              <div className="flex justify-end">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-3 py-1.5 text-xs font-semibold rounded-lg bg-brand-500 text-gray-950 hover:bg-brand-400 disabled:opacity-50 transition-colors"
                >
                  {saving ? "Saving..." : "Save"}
                </button>
              </div>
            )}
            <div className="relative">
              {isSystemSkill && (
                <div className="absolute top-2 right-2 text-[10px] text-gray-400 bg-gray-100 px-2 py-0.5 rounded">
                  Read-only
                </div>
              )}
              <textarea
                value={editorContent}
                onChange={(e) => setEditorContent(e.target.value)}
                readOnly={isSystemSkill}
                className={`w-full min-h-[500px] p-4 font-mono text-xs text-gray-700 leading-relaxed bg-gray-50 rounded-lg border border-gray-100 resize-y focus:outline-none focus:ring-2 focus:ring-brand-200 focus:border-brand-300 ${
                  isSystemSkill ? "cursor-default" : ""
                }`}
                spellCheck={false}
              />
            </div>
          </div>
        )}
      </div>

      {/* Chat input bar */}
      <div className="border-t border-gray-100 px-5 py-3 flex-shrink-0">
        <form onSubmit={handleChatSubmit} className="flex items-center gap-2">
          <div className="text-[10px] text-gray-400 mr-1 flex-shrink-0">{filePath}</div>
          <div className="flex-1 relative">
            <input
              type="text"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="Ask Loma"
              className="w-full px-3 py-2 text-sm text-gray-700 bg-gray-50 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-brand-200 focus:border-brand-300 placeholder:text-gray-400"
            />
          </div>
          <button
            type="submit"
            disabled={!chatInput.trim()}
            className="p-2 rounded-lg text-gray-400 hover:text-gray-600 disabled:opacity-30 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
            </svg>
          </button>
        </form>
      </div>
    </div>
  );
}
