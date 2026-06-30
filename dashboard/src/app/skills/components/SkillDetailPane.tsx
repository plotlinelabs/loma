"use client";

import { useState, useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { SkillDetailResponse } from "../../../lib/api";
import { basePath, skillAssetUrl, updateSkillFile } from "../../../lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Breadcrumb,
  BreadcrumbList,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { RiDownloadLine, RiSendPlaneLine } from "@remixicon/react";
import SkillEmptyState from "./SkillEmptyState";

const PROSE_CLASSES =
  "prose prose-sm max-w-none prose-headings:text-foreground prose-p:text-foreground/80 prose-a:text-primary prose-strong:text-foreground prose-code:text-primary prose-code:bg-primary/5 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-pre:bg-muted prose-pre:border prose-pre:border-border prose-li:text-foreground/80";

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
  const [editorContent, setEditorContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [chatInput, setChatInput] = useState("");
  const [activeTab, setActiveTab] = useState("viewer");

  const filePath = selectedFilePath || "SKILL.md";
  const isSystemSkill = skill?.scope === "system";

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

  function handleTabChange(value: string) {
    if (value === "editor") {
      setEditorContent(fileContent);
    }
    setActiveTab(value);
  }

  async function handleSave() {
    if (!skill || saving) return;
    const slug = skill.slug || skill.name;
    setSaving(true);
    try {
      await updateSkillFile(slug, filePath, editorContent);
      onSkillUpdated();
      setActiveTab("viewer");
    } catch {
      // silent
    } finally {
      setSaving(false);
    }
  }

  function handleDownload() {
    const blob = new Blob([activeTab === "editor" ? editorContent : fileContent], { type: "text/plain" });
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
    const userMessage = chatInput.trim();
    const prompt = `[Skill context: \`${slug}\`, file \`${filePath}\`. Use \`python3 tools/loma_skills.py dump --slug ${slug}\` to read it.]\n\n${userMessage}`;
    const params = new URLSearchParams({ prompt, autoSend: "true" });
    window.location.href = `${basePath}/chat?${params.toString()}`;
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-[13px] text-muted-foreground">Loading skill...</p>
      </div>
    );
  }

  if (!skill) {
    return <SkillEmptyState createUrl={createUrl} />;
  }

  const slug = skill.slug || skill.name;
  const scopeLabel = (skill.scope || "personal").charAt(0).toUpperCase() + (skill.scope || "personal").slice(1);

  return (
    <div className="flex-1 flex flex-col min-w-0 h-full">
      {/* Breadcrumb + actions */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-border flex-shrink-0">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink className="cursor-pointer" onClick={() => onNavigate("root")}>
                Skills
              </BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <span className="text-muted-foreground">{scopeLabel}</span>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbLink className="cursor-pointer" onClick={() => onNavigate("skill")}>
                {skill.name || slug}
              </BreadcrumbLink>
            </BreadcrumbItem>
            {selectedFilePath && (
              <>
                <BreadcrumbSeparator />
                <BreadcrumbItem>
                  <BreadcrumbPage>{selectedFilePath}</BreadcrumbPage>
                </BreadcrumbItem>
              </>
            )}
          </BreadcrumbList>
        </Breadcrumb>
        <div className="flex items-center gap-1">
          {activeTab === "editor" && (
            <Button variant="ghost" size="icon-xs" onClick={handleDownload} title="Download">
              <RiDownloadLine size={16} />
            </Button>
          )}
        </div>
      </div>

      {/* Content area */}
      {isAssetFile ? (
        <ScrollArea className="flex-1 px-6 py-5">
          {skill.files
            .filter((f) => f.path === filePath && f.kind === "local_asset")
            .map((file) => (
              <div key={file.path}>
                {file.content_type?.startsWith("image/") && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    src={skillAssetUrl(slug, file.path)}
                    alt={file.path}
                    className="max-w-full max-h-[520px] rounded-lg border border-border"
                  />
                )}
                {file.content_type === "application/pdf" && (
                  <iframe
                    src={skillAssetUrl(slug, file.path)}
                    className="w-full h-[520px] rounded-lg border border-border"
                  />
                )}
                {!file.content_type?.startsWith("image/") && file.content_type !== "application/pdf" && (
                  <a
                    href={skillAssetUrl(slug, file.path)}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[13px] text-primary hover:underline font-medium"
                  >
                    Download {file.path}
                  </a>
                )}
              </div>
            ))}
        </ScrollArea>
      ) : (
        <Tabs value={activeTab} onValueChange={handleTabChange} className="flex-1 flex flex-col min-h-0">
          <div className="px-5 border-b border-border flex-shrink-0">
            <TabsList variant="line">
              <TabsTrigger value="viewer">Viewer</TabsTrigger>
              <TabsTrigger value="editor">Editor</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="viewer" className="flex-1 overflow-y-auto px-6 py-5 m-0">
            {isMarkdown && Object.keys(frontmatter).length > 0 && (
              <div className="mb-6 space-y-2">
                {Object.entries(frontmatter).map(([key, value]) => (
                  <div key={key} className="flex gap-3">
                    <span className="text-xs text-muted-foreground min-w-[80px] pt-0.5">{key}</span>
                    <span className="text-[13px] text-foreground">{value}</span>
                  </div>
                ))}
              </div>
            )}
            {isMarkdown ? (
              <div className={PROSE_CLASSES}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripFrontmatter(fileContent)}</ReactMarkdown>
              </div>
            ) : (
              <pre className="whitespace-pre-wrap text-xs text-foreground/80 leading-relaxed bg-muted rounded-lg p-4 border border-border overflow-x-auto">
                {fileContent}
              </pre>
            )}
          </TabsContent>

          <TabsContent value="editor" className="flex-1 overflow-y-auto px-6 py-5 m-0">
            <div className="space-y-3">
              {!isSystemSkill && (
                <div className="flex justify-end">
                  <Button size="sm" onClick={handleSave} disabled={saving}>
                    {saving ? "Saving..." : "Save"}
                  </Button>
                </div>
              )}
              <div className="relative">
                {isSystemSkill && (
                  <Badge variant="secondary" className="absolute top-2 right-2 text-[10px]">
                    Read-only
                  </Badge>
                )}
                <Textarea
                  value={editorContent}
                  onChange={(e) => setEditorContent(e.target.value)}
                  readOnly={isSystemSkill}
                  className={cn(
                    "min-h-[500px] font-mono text-xs leading-relaxed resize-y",
                    isSystemSkill && "cursor-default"
                  )}
                  spellCheck={false}
                />
              </div>
            </div>
          </TabsContent>
        </Tabs>
      )}

      {/* Chat input bar */}
      <div className="border-t border-border px-5 py-3 flex-shrink-0">
        <form onSubmit={handleChatSubmit} className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground mr-1 flex-shrink-0">{filePath}</span>
          <Input
            type="text"
            value={chatInput}
            onChange={(e) => setChatInput(e.target.value)}
            placeholder="Ask Loma"
            className="flex-1"
          />
          <Button
            type="submit"
            variant="ghost"
            size="icon-xs"
            disabled={!chatInput.trim()}
          >
            <RiSendPlaneLine size={16} />
          </Button>
        </form>
      </div>
    </div>
  );
}
