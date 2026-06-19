"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  fetchSkill,
  fetchSkillHistory,
  fetchSkillVersion,
  fetchSkillDiff,
  basePath,
} from "../../../lib/api";
import type { SkillDetailResponse, SkillCommit } from "../../../lib/api";
import { DiffViewer } from "../../../components/DiffViewer";

/* ── Helpers ─────────────────────────────────────────────────────── */

function formatSkillName(name: string): string {
  return name
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function stripFrontmatter(content: string): string {
  if (!content.startsWith("---")) return content;
  const end = content.indexOf("---", 3);
  if (end === -1) return content;
  return content.slice(end + 3).trimStart();
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

function authorInitial(name: string): string {
  return name.charAt(0).toUpperCase();
}

const PROSE_CLASSES =
  "prose prose-sm max-w-none prose-headings:text-gray-900 prose-p:text-gray-700 prose-a:text-brand-600 prose-strong:text-gray-800 prose-code:text-brand-700 prose-code:bg-brand-50 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs prose-pre:bg-gray-50 prose-pre:border prose-pre:border-gray-100 prose-li:text-gray-700 prose-table:text-sm";

/* ── History Panel ───────────────────────────────────────────────── */

type ViewMode = "content" | "diff";

function HistoryPanel({
  commits,
  loading,
  activeVersion,
  viewMode,
  compareTarget,
  onSelectVersion,
  onResetVersion,
  onToggleViewMode,
  onChangeCompareTarget,
}: {
  commits: SkillCommit[];
  loading: boolean;
  activeVersion: string | null;
  viewMode: ViewMode;
  compareTarget: string;
  onSelectVersion: (sha: string) => void;
  onResetVersion: () => void;
  onToggleViewMode: (mode: ViewMode) => void;
  onChangeCompareTarget: (sha: string) => void;
}) {
  if (loading) {
    return (
      <div className="bg-surface border border-gray-200 rounded-xl p-5">
        <div className="skeleton h-4 w-20 rounded mb-4" />
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex gap-3">
              <div className="skeleton w-7 h-7 rounded-full flex-shrink-0" />
              <div className="flex-1 space-y-1.5">
                <div className="skeleton h-3 w-24 rounded" />
                <div className="skeleton h-2.5 w-full rounded" />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (commits.length === 0) {
    return (
      <div className="bg-surface border border-gray-200 rounded-xl p-5">
        <h3 className="text-sm font-semibold text-gray-900 mb-2">History</h3>
        <p className="text-xs text-gray-400">No git history found.</p>
      </div>
    );
  }

  return (
    <div className="bg-surface border border-gray-200 rounded-xl p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-gray-900">History</h3>
        <span className="text-[10px] text-gray-400 font-medium">
          {commits.length} commit{commits.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Back + view mode toggle when viewing a version */}
      {activeVersion && (
        <div className="space-y-2 mb-3">
          <button
            onClick={onResetVersion}
            className="w-full text-[11px] font-medium text-brand-600 bg-brand-50 border border-brand-200 rounded-lg px-3 py-1.5 hover:bg-brand-100 transition-colors text-left flex items-center gap-1.5"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 15 3 9m0 0 6-6M3 9h12a6 6 0 0 1 0 12h-3" />
            </svg>
            Back to current
          </button>

          {/* Content / Diff toggle */}
          <div className="flex rounded-lg border border-gray-200 overflow-hidden">
            <button
              onClick={() => onToggleViewMode("content")}
              className={`flex-1 text-[10px] font-semibold py-1.5 transition-colors ${
                viewMode === "content"
                  ? "bg-[#1F1D1A] dark:bg-[#E8E4DD] text-[#FDFBF7] dark:text-[#0A0A0A]"
                  : "bg-surface text-gray-500 hover:bg-gray-50"
              }`}
            >
              Content
            </button>
            <button
              onClick={() => onToggleViewMode("diff")}
              className={`flex-1 text-[10px] font-semibold py-1.5 transition-colors border-l border-gray-200 ${
                viewMode === "diff"
                  ? "bg-[#1F1D1A] dark:bg-[#E8E4DD] text-[#FDFBF7] dark:text-[#0A0A0A]"
                  : "bg-surface text-gray-500 hover:bg-gray-50"
              }`}
            >
              Diff
            </button>
          </div>

          {/* Compare target selector (only in diff mode) */}
          {viewMode === "diff" && (
            <div>
              <label className="text-[10px] text-gray-400 font-medium block mb-1">
                Compare with
              </label>
              <select
                value={compareTarget}
                onChange={(e) => onChangeCompareTarget(e.target.value)}
                className="w-full text-[11px] text-gray-700 border border-gray-200 rounded-lg px-2.5 py-1.5 bg-surface focus:outline-none focus:border-accent-200 focus:ring-1 focus:ring-accent-200"
              >
                <option value="HEAD">Current version</option>
                {commits
                  .filter((c) => c.sha !== activeVersion)
                  .map((c) => (
                    <option key={c.sha} value={c.sha}>
                      {c.sha.slice(0, 7)} — {c.message.slice(0, 40)}{c.message.length > 40 ? "…" : ""}
                    </option>
                  ))}
              </select>
            </div>
          )}
        </div>
      )}

      {/* Timeline */}
      <div className="relative">
        <div className="absolute left-[13px] top-3 bottom-3 w-px bg-gray-200" />

        <div className="space-y-0">
          {commits.map((commit, i) => {
            const isFirst = i === 0;
            const isActive = activeVersion === commit.sha;

            return (
              <div key={commit.sha} className="relative flex gap-3 py-2.5 group">
                {/* Timeline dot */}
                <div
                  className={`relative z-10 w-[26px] h-[26px] rounded-full flex items-center justify-center flex-shrink-0 text-[10px] font-bold border-2 transition-colors
                    ${isActive
                      ? "bg-brand-100 border-brand-500 text-brand-700"
                      : isFirst
                        ? "bg-green-50 border-green-400 text-green-700"
                        : "bg-surface border-gray-200 text-gray-400"
                    }`}
                >
                  {authorInitial(commit.author)}
                </div>

                {/* Commit info */}
                <div className="flex-1 min-w-0 pt-0.5">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] font-semibold text-gray-800 truncate">
                      {commit.author}
                    </span>
                    <span className="text-[10px] text-gray-400 flex-shrink-0">
                      {relativeTime(commit.date)}
                    </span>
                  </div>
                  <p className="text-[11px] text-gray-500 truncate mt-0.5 leading-tight">
                    {commit.message}
                  </p>

                  {/* Action buttons */}
                  {!isActive && (
                    <div className="mt-1 flex gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={() => onSelectVersion(commit.sha)}
                        className="text-[10px] font-medium text-gray-400 hover:text-brand-600 transition-colors flex items-center gap-1"
                      >
                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
                          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
                        </svg>
                        View
                      </button>
                    </div>
                  )}
                  {isActive && (
                    <span className="mt-1 text-[10px] font-medium text-brand-600 flex items-center gap-1">
                      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                      </svg>
                      Viewing
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ── Page ─────────────────────────────────────────────────────────── */

export default function SkillDetailPage() {
  const params = useParams();
  const name = params.name as string;

  const [skill, setSkill] = useState<SkillDetailResponse | null>(null);
  const [commits, setCommits] = useState<SkillCommit[]>([]);
  const [loading, setLoading] = useState(true);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Version viewing state
  const [activeVersion, setActiveVersion] = useState<string | null>(null);
  const [versionContent, setVersionContent] = useState<string | null>(null);
  const [versionLoading, setVersionLoading] = useState(false);
  const [versionCommit, setVersionCommit] = useState<SkillCommit | null>(null);

  // Diff state
  const [viewMode, setViewMode] = useState<ViewMode>("content");
  const [compareTarget, setCompareTarget] = useState("HEAD");
  const [diffText, setDiffText] = useState("");
  const [diffLoading, setDiffLoading] = useState(false);

  useEffect(() => {
    if (!name) return;

    setLoading(true);
    setHistoryLoading(true);

    fetchSkill(name)
      .then(setSkill)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));

    fetchSkillHistory(name)
      .then((data) => setCommits(data.commits))
      .catch(() => setCommits([]))
      .finally(() => setHistoryLoading(false));
  }, [name]);

  // Fetch diff when mode, version, or compare target changes
  const loadDiff = useCallback(
    (sha: string, target: string) => {
      setDiffLoading(true);
      fetchSkillDiff(name, sha, target)
        .then((data) => setDiffText(data.diff))
        .catch(() => setDiffText("Failed to load diff."))
        .finally(() => setDiffLoading(false));
    },
    [name],
  );

  const handleSelectVersion = (sha: string) => {
    const commit = commits.find((c) => c.sha === sha);
    setActiveVersion(sha);
    setVersionCommit(commit || null);
    setViewMode("content");
    setCompareTarget("HEAD");
    setVersionLoading(true);

    fetchSkillVersion(name, sha)
      .then((data) => setVersionContent(data.content))
      .catch(() => setVersionContent("Failed to load this version."))
      .finally(() => setVersionLoading(false));
  };

  const handleResetVersion = () => {
    setActiveVersion(null);
    setVersionContent(null);
    setVersionCommit(null);
    setViewMode("content");
    setDiffText("");
  };

  const handleToggleViewMode = (mode: ViewMode) => {
    setViewMode(mode);
    if (mode === "diff" && activeVersion) {
      loadDiff(activeVersion, compareTarget);
    }
  };

  const handleChangeCompareTarget = (target: string) => {
    setCompareTarget(target);
    if (activeVersion) {
      loadDiff(activeVersion, target);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <div className="flex items-center gap-2 text-gray-400">
          <svg className="animate-spin w-4 h-4 text-brand-600" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Loading skill...
        </div>
      </div>
    );
  }

  if (error || !skill) {
    return (
      <div className="text-red-600 text-center py-20 bg-red-50 rounded-xl border border-red-200">
        {error || "Skill not found"}
      </div>
    );
  }

  const currentBody = stripFrontmatter(skill.content);
  const displayBody =
    activeVersion && versionContent ? stripFrontmatter(versionContent) : currentBody;
  const extraFileNames = Object.keys(skill.extra_files);

  return (
    <div className="space-y-4">
      {/* Back link */}
      <a
        href={`${basePath}/skills`}
        className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-brand-600 transition-colors font-medium"
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.5 19.5 3 12m0 0 7.5-7.5M3 12h18" />
        </svg>
        Back to skills
      </a>

      {/* Two-column layout */}
      <div className="flex flex-col md:flex-row gap-5">
        {/* Left: Skill content */}
        <div className="flex-1 min-w-0 space-y-5">
          <div className="bg-surface border border-gray-200 rounded-xl p-6">
            <div className="flex items-center gap-3 mb-5">
              <span className="text-xs px-2.5 py-1 rounded-full font-medium border bg-brand-100 text-brand-700 border-brand-200">
                Skill
              </span>
              <h1 className="text-xl font-semibold text-gray-900">
                {formatSkillName(name)}
              </h1>
            </div>

            {/* Version banner */}
            {activeVersion && versionCommit && (
              <div className="mb-5 flex items-center gap-2.5 px-4 py-2.5 bg-amber-50 border border-amber-200 rounded-lg">
                <svg className="w-4 h-4 text-amber-500 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
                </svg>
                <div className="flex-1 min-w-0">
                  <span className="text-xs font-medium text-amber-800">
                    {viewMode === "diff" ? "Comparing" : "Viewing"} version from{" "}
                    {relativeTime(versionCommit.date)} by {versionCommit.author}
                  </span>
                  <span className="text-[10px] text-amber-600 ml-2 font-mono">
                    {versionCommit.sha.slice(0, 7)}
                  </span>
                  {viewMode === "diff" && (
                    <span className="text-[10px] text-amber-600 ml-1">
                      → {compareTarget === "HEAD" ? "current" : compareTarget.slice(0, 7)}
                    </span>
                  )}
                </div>
                <button
                  onClick={handleResetVersion}
                  className="text-[11px] font-medium text-amber-700 hover:text-amber-900 transition-colors flex-shrink-0"
                >
                  Back to current
                </button>
              </div>
            )}

            {/* Skill content or diff view */}
            {viewMode === "diff" && activeVersion ? (
              <DiffViewer diff={diffText} loading={diffLoading} />
            ) : versionLoading ? (
              <div className="space-y-3">
                <div className="skeleton h-4 w-3/4 rounded" />
                <div className="skeleton h-3 w-full rounded" />
                <div className="skeleton h-3 w-5/6 rounded" />
                <div className="skeleton h-3 w-full rounded" />
                <div className="skeleton h-3 w-2/3 rounded" />
              </div>
            ) : (
              <div className={PROSE_CLASSES}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{displayBody}</ReactMarkdown>
              </div>
            )}
          </div>

          {/* Extra files (only show for current version in content mode) */}
          {!activeVersion && extraFileNames.length > 0 && (
            <div className="space-y-4">
              <h2 className="text-sm font-semibold text-gray-900">
                Additional Files ({extraFileNames.length})
              </h2>
              {extraFileNames.map((fileName) => {
                const content = skill.extra_files[fileName];
                const isMarkdown = fileName.endsWith(".md");

                return (
                  <div key={fileName} className="bg-surface border border-gray-200 rounded-xl p-5">
                    <div className="flex items-center gap-2 mb-3">
                      <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
                      </svg>
                      <span className="text-sm font-medium text-gray-700">{fileName}</span>
                    </div>
                    {isMarkdown ? (
                      <div className={PROSE_CLASSES}>
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {stripFrontmatter(content)}
                        </ReactMarkdown>
                      </div>
                    ) : (
                      <pre className="whitespace-pre-wrap text-xs text-gray-600 leading-relaxed bg-gray-50 rounded-lg p-4 border border-gray-100 overflow-x-auto max-h-96 overflow-y-auto">
                        {content}
                      </pre>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right: History panel */}
        <div className="w-full md:w-[300px] flex-shrink-0">
          <div className="md:sticky md:top-4">
            <HistoryPanel
              commits={commits}
              loading={historyLoading}
              activeVersion={activeVersion}
              viewMode={viewMode}
              compareTarget={compareTarget}
              onSelectVersion={handleSelectVersion}
              onResetVersion={handleResetVersion}
              onToggleViewMode={handleToggleViewMode}
              onChangeCompareTarget={handleChangeCompareTarget}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
