"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  basePath,
  fetchSkill,
  fetchSkillHistory,
  skillAssetUrl,
} from "../../../lib/api";
import type { SkillCommit, SkillDetailResponse, SkillFile } from "../../../lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Separator } from "@/components/ui/separator";
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage, BreadcrumbSeparator } from "@/components/ui/breadcrumb";

const PROSE_CLASSES =
  "prose prose-sm max-w-none prose-headings:text-foreground prose-p:text-muted-foreground prose-a:text-brand-600 prose-strong:text-foreground prose-code:text-brand-700 prose-code:bg-brand-50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-pre:bg-muted prose-pre:border prose-pre:border-border prose-li:text-muted-foreground";

function isAsset(file: SkillFile): boolean {
  return file.kind === "local_asset";
}

function formatBytes(size?: number): string {
  if (!size) return "-";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function stripFrontmatter(content: string): string {
  if (!content.startsWith("---")) return content;
  const end = content.indexOf("---", 3);
  if (end === -1) return content;
  return content.slice(end + 3).trimStart();
}

function chatUrl(prompt: string): string {
  return `${basePath}/chat?prompt=${encodeURIComponent(prompt)}`;
}

function skillSlug(skill: SkillDetailResponse, fallback: string): string {
  return skill.slug || fallback;
}

function buildFileList(skill: SkillDetailResponse): string {
  return skill.files
    .map((file) => `- ${file.path} (${file.kind}${file.content_type ? `, ${file.content_type}` : ""})`)
    .join("\n");
}

function buildEditSkillPrompt(skill: SkillDetailResponse, fallbackSlug: string): string {
  const slug = skillSlug(skill, fallbackSlug);
  return [
    `I want to edit the Loma skill \`${slug}\`.`,
    "",
    `Skill name: ${skill.name || slug}`,
    `Description: ${skill.description || "No description"}`,
    "",
    "Current files:",
    buildFileList(skill) || "- SKILL.md",
    "",
    `First fetch the full skill with \`python3 tools/loma_skills.py dump --slug ${slug}\` and inspect any specific supporting file you need with \`python3 tools/loma_skills.py file --slug ${slug} --path <path>\` or \`python3 tools/loma_skills.py asset --slug ${slug} --path <path>\`.`,
    "",
    "Help me decide the exact change. Only update the live skill after I explicitly confirm what to change.",
  ].join("\n");
}

function buildEditSkillFilePrompt(skill: SkillDetailResponse, fallbackSlug: string, file: SkillFile): string {
  const slug = skillSlug(skill, fallbackSlug);
  const readCommand = file.kind === "local_asset"
    ? `python3 tools/loma_skills.py asset --slug ${slug} --path ${file.path}`
    : `python3 tools/loma_skills.py file --slug ${slug} --path ${file.path}`;
  return [
    `I want to edit \`${file.path}\` in the Loma skill \`${slug}\`.`,
    "",
    `File kind: ${file.kind}`,
    `Content type: ${file.content_type || "unknown"}`,
    "",
    `First inspect it with \`${readCommand}\` and fetch the whole skill with \`python3 tools/loma_skills.py dump --slug ${slug}\` if you need broader context.`,
    "",
    "Help me decide the exact change. Only update the live skill after I explicitly confirm what to change.",
  ].join("\n");
}

function renderTextFile(path: string, content: string) {
  const isMarkdown = path.endsWith(".md") || path === "SKILL.md";
  if (isMarkdown) {
    return (
      <div className={PROSE_CLASSES}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{stripFrontmatter(content)}</ReactMarkdown>
      </div>
    );
  }
  return (
    <pre className="whitespace-pre-wrap text-xs text-muted-foreground leading-relaxed bg-muted rounded-lg p-3 border border-border overflow-x-auto max-h-[520px] overflow-y-auto">
      {content}
    </pre>
  );
}

export default function SkillDetailPage() {
  const params = useParams();
  const name = params.name as string;

  const [skill, setSkill] = useState<SkillDetailResponse | null>(null);
  const [history, setHistory] = useState<SkillCommit[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!name) return;
    setLoading(true);
    Promise.all([fetchSkill(name), fetchSkillHistory(name)])
      .then(([skillData, historyData]) => {
        setSkill(skillData);
        setHistory(historyData.commits);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load skill"))
      .finally(() => setLoading(false));
  }, [name]);

  const files = useMemo(() => {
    if (!skill) return [];
    return [...skill.files].sort((a, b) => a.path.localeCompare(b.path));
  }, [skill]);

  if (loading) {
    return <div className="py-20 text-center text-[13px] text-muted-foreground">Loading skill...</div>;
  }

  if (!skill) {
    return <div className="py-20 text-center text-[13px] text-destructive">{error || "Skill not found"}</div>;
  }

  const slug = skillSlug(skill, name);
  const textFiles = files.filter((file) => !isAsset(file));
  const assetFiles = files.filter(isAsset);

  return (
    <div className="space-y-3 animate-fade-in-up">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href="/skills">Skills</BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator />
          <BreadcrumbItem>
            <BreadcrumbPage>{skill.name || slug}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>

      <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-3">
        <div>
          <h1 className="text-xl font-heading font-semibold text-foreground">{skill.name || slug}</h1>
          <p className="text-[13px] text-muted-foreground mt-1">{skill.description || "No description yet."}</p>
          <div className="flex flex-wrap gap-1.5 mt-2">
            <Badge variant="secondary" className="font-mono">{slug}</Badge>
            {skill.tags?.map((tag) => (
              <Badge key={tag} className="bg-brand-50 text-brand-700 border-transparent">{tag}</Badge>
            ))}
          </div>
        </div>
        <Button asChild>
          <Link href={chatUrl(buildEditSkillPrompt(skill, name))}>
            Edit in Chat
          </Link>
        </Button>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_300px] gap-5">
        <div className="space-y-3 min-w-0">
          <Card>
            <CardContent>
              <div className="flex items-center justify-between gap-2 mb-2">
                <h2 className="text-[13px] font-heading font-semibold text-foreground">SKILL.md</h2>
                <Button variant="link" size="xs" asChild>
                  <Link href={chatUrl(buildEditSkillFilePrompt(skill, name, files.find((file) => file.path === "SKILL.md") || { path: "SKILL.md", kind: "inline_text" }))}>
                    Edit file in chat
                  </Link>
                </Button>
              </div>
              {renderTextFile("SKILL.md", skill.content)}
            </CardContent>
          </Card>

          {textFiles.filter((file) => file.path !== "SKILL.md").map((file) => {
            const content = skill.extra_files[file.path] || "";
            return (
              <Card key={file.path}>
                <CardContent>
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <div>
                      <h2 className="text-[13px] font-heading font-semibold text-foreground font-mono">{file.path}</h2>
                      <p className="text-xs text-muted-foreground mt-1">{file.content_type || "text"} · {formatBytes(file.size_bytes)}</p>
                    </div>
                    <Button variant="link" size="xs" asChild>
                      <Link href={chatUrl(buildEditSkillFilePrompt(skill, name, file))}>
                        Edit file in chat
                      </Link>
                    </Button>
                  </div>
                  {renderTextFile(file.path, content)}
                </CardContent>
              </Card>
            );
          })}

          {assetFiles.length > 0 && (
            <Card>
              <CardContent>
                <h2 className="text-[13px] font-heading font-semibold text-foreground mb-2">Assets</h2>
                <div className="space-y-4">
                  {assetFiles.map((file) => (
                    <div key={file.path} className="border border-border rounded-lg p-3 space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <h3 className="text-[13px] font-semibold text-foreground font-mono truncate">{file.path}</h3>
                          <p className="text-xs text-muted-foreground mt-1">{file.content_type || "asset"} · {formatBytes(file.size_bytes)}</p>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <Button variant="ghost" size="xs" asChild>
                            <a href={skillAssetUrl(slug, file.path)} target="_blank" rel="noreferrer">
                              Open
                            </a>
                          </Button>
                          <Button variant="link" size="xs" asChild>
                            <Link href={chatUrl(buildEditSkillFilePrompt(skill, name, file))}>
                              Edit in chat
                            </Link>
                          </Button>
                        </div>
                      </div>
                      {file.content_type?.startsWith("image/") && (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={skillAssetUrl(slug, file.path)} alt={file.path} className="max-w-full max-h-[420px] rounded-lg border border-border" />
                      )}
                      {file.content_type === "application/pdf" && (
                        <iframe src={skillAssetUrl(slug, file.path)} className="w-full h-[520px] rounded-lg border border-border" />
                      )}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}
        </div>

        <aside className="space-y-3">
          <Card>
            <CardContent>
              <h2 className="text-[13px] font-heading font-semibold text-foreground mb-2">Package</h2>
              <div className="space-y-2">
                {files.map((file) => (
                  <div key={file.path} className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-mono text-muted-foreground truncate">{file.path}</span>
                    <span className="text-muted-foreground flex-shrink-0">{file.kind === "local_asset" ? "asset" : "text"}</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent>
              <h2 className="text-[13px] font-heading font-semibold text-foreground mb-2">History</h2>
              {history.length === 0 ? (
                <p className="text-xs text-muted-foreground">No versions yet.</p>
              ) : (
                <div className="space-y-2">
                  {history.slice(0, 20).map((commit) => (
                    <div key={commit.sha} className="border-b border-border pb-3 last:border-0">
                      <p className="text-xs font-medium text-foreground">{commit.message}</p>
                      <p className="text-[11px] text-muted-foreground mt-1">{commit.author} · {new Date(commit.date).toLocaleString()}</p>
                      <p className="text-[10px] font-mono text-muted-foreground/50 mt-1">{commit.sha.slice(0, 10)}</p>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
