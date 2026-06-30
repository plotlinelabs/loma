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

const PROSE_CLASSES =
  "prose prose-sm max-w-none prose-headings:text-gray-900 prose-p:text-gray-700 prose-a:text-brand-600 prose-strong:text-gray-800 prose-code:text-brand-700 prose-code:bg-brand-50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-pre:bg-gray-50 prose-pre:border prose-pre:border-gray-100 prose-li:text-gray-700";

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
    <pre className="whitespace-pre-wrap text-xs text-gray-700 leading-relaxed bg-gray-50 rounded-lg p-4 border border-gray-100 overflow-x-auto max-h-[520px] overflow-y-auto">
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
    return <div className="py-20 text-center text-sm text-gray-400">Loading skill...</div>;
  }

  if (!skill) {
    return <div className="py-20 text-center text-sm text-red-600">{error || "Skill not found"}</div>;
  }

  const slug = skillSlug(skill, name);
  const textFiles = files.filter((file) => !isAsset(file));
  const assetFiles = files.filter(isAsset);

  return (
    <div className="space-y-5 animate-fade-in-up">
      <Link href="/skills" className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-brand-600 transition-colors font-medium">
        Back to skills
      </Link>

      <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{skill.name || slug}</h1>
          <p className="text-sm text-gray-500 mt-1">{skill.description || "No description yet."}</p>
          <div className="flex flex-wrap gap-1.5 mt-3">
            <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-md font-mono">{slug}</span>
            {skill.tags?.map((tag) => (
              <span key={tag} className="text-xs bg-brand-50 text-brand-700 px-2 py-0.5 rounded-md">{tag}</span>
            ))}
          </div>
        </div>
        <Link
          href={chatUrl(buildEditSkillPrompt(skill, name))}
          className="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold rounded-lg bg-brand-500 text-gray-950 hover:bg-brand-400 transition-colors"
        >
          Edit in Chat
        </Link>
      </div>

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_300px] gap-5">
        <div className="space-y-5 min-w-0">
          <section className="bg-surface border border-gray-200 rounded-xl p-5">
            <div className="flex items-center justify-between gap-3 mb-4">
              <h2 className="text-sm font-semibold text-gray-900">SKILL.md</h2>
              <Link href={chatUrl(buildEditSkillFilePrompt(skill, name, files.find((file) => file.path === "SKILL.md") || { path: "SKILL.md", kind: "inline_text" }))} className="text-xs font-semibold text-brand-700 hover:text-brand-800">
                Edit file in chat
              </Link>
            </div>
            {renderTextFile("SKILL.md", skill.content)}
          </section>

          {textFiles.filter((file) => file.path !== "SKILL.md").map((file) => {
            const content = skill.extra_files[file.path] || "";
            return (
              <section key={file.path} className="bg-surface border border-gray-200 rounded-xl p-5">
                <div className="flex items-center justify-between gap-3 mb-4">
                  <div>
                    <h2 className="text-sm font-semibold text-gray-900 font-mono">{file.path}</h2>
                    <p className="text-xs text-gray-400 mt-1">{file.content_type || "text"} · {formatBytes(file.size_bytes)}</p>
                  </div>
                  <Link href={chatUrl(buildEditSkillFilePrompt(skill, name, file))} className="text-xs font-semibold text-brand-700 hover:text-brand-800">
                    Edit file in chat
                  </Link>
                </div>
                {renderTextFile(file.path, content)}
              </section>
            );
          })}

          {assetFiles.length > 0 && (
            <section className="bg-surface border border-gray-200 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-gray-900 mb-4">Assets</h2>
              <div className="space-y-4">
                {assetFiles.map((file) => (
                  <div key={file.path} className="border border-gray-100 rounded-lg p-4 space-y-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <h3 className="text-sm font-semibold text-gray-900 font-mono truncate">{file.path}</h3>
                        <p className="text-xs text-gray-400 mt-1">{file.content_type || "asset"} · {formatBytes(file.size_bytes)}</p>
                      </div>
                      <div className="flex items-center gap-3 flex-shrink-0">
                        <a href={skillAssetUrl(slug, file.path)} target="_blank" rel="noreferrer" className="text-xs font-semibold text-gray-600 hover:text-gray-900">
                          Open
                        </a>
                        <Link href={chatUrl(buildEditSkillFilePrompt(skill, name, file))} className="text-xs font-semibold text-brand-700 hover:text-brand-800">
                          Edit in chat
                        </Link>
                      </div>
                    </div>
                    {file.content_type?.startsWith("image/") && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={skillAssetUrl(slug, file.path)} alt={file.path} className="max-w-full max-h-[420px] rounded-lg border border-gray-200" />
                    )}
                    {file.content_type === "application/pdf" && (
                      <iframe src={skillAssetUrl(slug, file.path)} className="w-full h-[520px] rounded-lg border border-gray-200" />
                    )}
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        <aside className="space-y-5">
          <section className="bg-surface border border-gray-200 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">Package</h2>
            <div className="space-y-2">
              {files.map((file) => (
                <div key={file.path} className="flex items-center justify-between gap-3 text-xs">
                  <span className="font-mono text-gray-600 truncate">{file.path}</span>
                  <span className="text-gray-400 flex-shrink-0">{file.kind === "local_asset" ? "asset" : "text"}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="bg-surface border border-gray-200 rounded-xl p-5">
            <h2 className="text-sm font-semibold text-gray-900 mb-3">History</h2>
            {history.length === 0 ? (
              <p className="text-xs text-gray-400">No versions yet.</p>
            ) : (
              <div className="space-y-3">
                {history.slice(0, 20).map((commit) => (
                  <div key={commit.sha} className="border-b border-gray-100 pb-3 last:border-0">
                    <p className="text-xs font-medium text-gray-800">{commit.message}</p>
                    <p className="text-[11px] text-gray-400 mt-1">{commit.author} · {new Date(commit.date).toLocaleString()}</p>
                    <p className="text-[10px] font-mono text-gray-300 mt-1">{commit.sha.slice(0, 10)}</p>
                  </div>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}
