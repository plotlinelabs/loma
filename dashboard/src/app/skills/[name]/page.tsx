"use client";

import { useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  basePath,
  deleteSkill,
  deleteSkillFile,
  fetchSkill,
  fetchSkillHistory,
  skillAssetUrl,
  updateSkillFile,
  uploadSkillAsset,
} from "../../../lib/api";
import type { SkillCommit, SkillDetailResponse, SkillFile } from "../../../lib/api";

function isAsset(file: SkillFile): boolean {
  return file.kind === "local_asset";
}

function formatBytes(size?: number): string {
  if (!size) return "-";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export default function SkillDetailPage() {
  const params = useParams();
  const router = useRouter();
  const name = params.name as string;

  const [skill, setSkill] = useState<SkillDetailResponse | null>(null);
  const [history, setHistory] = useState<SkillCommit[]>([]);
  const [selectedPath, setSelectedPath] = useState("SKILL.md");
  const [draft, setDraft] = useState("");
  const [newPath, setNewPath] = useState("");
  const [assetPath, setAssetPath] = useState("");
  const [assetFile, setAssetFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = () => {
    if (!name) return;
    setLoading(true);
    Promise.all([fetchSkill(name), fetchSkillHistory(name)])
      .then(([skillData, historyData]) => {
        setSkill(skillData);
        setHistory(historyData.commits);
        const file = skillData.files.find((f) => f.path === selectedPath) || skillData.files.find((f) => f.path === "SKILL.md");
        const path = file?.path || "SKILL.md";
        setSelectedPath(path);
        setDraft(path === "SKILL.md" ? skillData.content : skillData.extra_files[path] || "");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load skill"))
      .finally(() => setLoading(false));
  };

  useEffect(load, [name]);

  const files = useMemo(() => {
    if (!skill) return [];
    return [...skill.files].sort((a, b) => a.path.localeCompare(b.path));
  }, [skill]);

  const selectedFile = files.find((f) => f.path === selectedPath);

  const selectFile = (path: string) => {
    if (!skill) return;
    setSelectedPath(path);
    setNotice(null);
    setError(null);
    setDraft(path === "SKILL.md" ? skill.content : skill.extra_files[path] || "");
  };

  const refreshSkill = (updated: SkillDetailResponse) => {
    setSkill(updated);
    setDraft(selectedPath === "SKILL.md" ? updated.content : updated.extra_files[selectedPath] || "");
    fetchSkillHistory(name).then((data) => setHistory(data.commits)).catch(() => {});
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const updated = await updateSkillFile(name, selectedPath, draft);
      refreshSkill(updated);
      setNotice("Saved live.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save file");
    } finally {
      setSaving(false);
    }
  };

  const handleAddTextFile = async () => {
    const path = newPath.trim();
    if (!path) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await updateSkillFile(name, path, "");
      refreshSkill(updated);
      setSelectedPath(path);
      setDraft("");
      setNewPath("");
      setNotice("Text file added.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add file");
    } finally {
      setSaving(false);
    }
  };

  const handleUploadAsset = async () => {
    if (!assetPath.trim() || !assetFile) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await uploadSkillAsset(name, assetPath.trim(), assetFile);
      refreshSkill(updated);
      setAssetPath("");
      setAssetFile(null);
      setNotice("Asset uploaded.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to upload asset");
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteFile = async (path: string) => {
    if (path === "SKILL.md" || !confirm(`Remove ${path} from this skill?`)) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await deleteSkillFile(name, path);
      refreshSkill(updated);
      setSelectedPath("SKILL.md");
      setDraft(updated.content);
      setNotice("File removed from live skill.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to remove file");
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteSkill = async () => {
    if (!confirm(`Delete ${name}? The skill is disabled but version history is retained.`)) return;
    setSaving(true);
    try {
      await deleteSkill(name);
      router.push("/skills");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete skill");
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="py-20 text-center text-sm text-gray-400">Loading skill...</div>;
  }

  if (!skill) {
    return <div className="py-20 text-center text-sm text-red-600">{error || "Skill not found"}</div>;
  }

  return (
    <div className="space-y-4">
      <a href={`${basePath}/skills`} className="inline-flex items-center gap-1.5 text-sm text-gray-500 hover:text-brand-600 font-medium">
        Back to skills
      </a>

      <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-gray-900">{skill.slug || name}</h1>
          <p className="text-sm text-gray-500 mt-1">{skill.description || "No description yet."}</p>
        </div>
        <button
          onClick={handleDeleteSkill}
          disabled={saving}
          className="px-3 py-2 text-sm font-semibold rounded-lg border border-red-200 text-red-700 bg-red-50 hover:bg-red-100 disabled:opacity-50"
        >
          Delete Skill
        </button>
      </div>

      {(error || notice) && (
        <div className={`text-sm rounded-lg px-4 py-3 border ${error ? "text-red-700 bg-red-50 border-red-200" : "text-green-700 bg-green-50 border-green-200"}`}>
          {error || notice}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr_280px] gap-4">
        <div className="bg-surface border border-gray-200 rounded-xl p-4 space-y-4">
          <div>
            <h2 className="text-sm font-semibold text-gray-900 mb-3">Files</h2>
            <div className="space-y-1">
              {files.map((file) => (
                <div key={file.path} className="flex items-center gap-2">
                  <button
                    onClick={() => selectFile(file.path)}
                    className={`flex-1 min-w-0 text-left px-2.5 py-2 rounded-lg text-xs font-mono ${
                      selectedPath === file.path ? "bg-brand-50 text-brand-800" : "hover:bg-gray-50 text-gray-600"
                    }`}
                  >
                    <span className="truncate block">{file.path}</span>
                    {isAsset(file) && <span className="text-[10px] text-gray-400">{formatBytes(file.size_bytes)}</span>}
                  </button>
                  {file.path !== "SKILL.md" && (
                    <button onClick={() => handleDeleteFile(file.path)} className="text-xs text-gray-400 hover:text-red-600 px-1">
                      Remove
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>

          <div className="border-t border-gray-100 pt-4 space-y-2">
            <input
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
              placeholder="notes/reference.md"
              className="w-full px-3 py-2 text-xs border border-gray-200 rounded-lg bg-surface"
            />
            <button onClick={handleAddTextFile} disabled={saving} className="w-full px-3 py-2 text-xs font-semibold rounded-lg bg-gray-900 text-white disabled:opacity-50">
              Add Text File
            </button>
          </div>

          <div className="border-t border-gray-100 pt-4 space-y-2">
            <input
              value={assetPath}
              onChange={(e) => setAssetPath(e.target.value)}
              placeholder="assets/example.pdf"
              className="w-full px-3 py-2 text-xs border border-gray-200 rounded-lg bg-surface"
            />
            <input
              type="file"
              onChange={(e) => setAssetFile(e.target.files?.[0] || null)}
              className="w-full text-xs text-gray-500"
            />
            <button onClick={handleUploadAsset} disabled={saving || !assetFile} className="w-full px-3 py-2 text-xs font-semibold rounded-lg bg-gray-900 text-white disabled:opacity-50">
              Upload Asset
            </button>
          </div>
        </div>

        <div className="bg-surface border border-gray-200 rounded-xl p-4 min-h-[520px]">
          {selectedFile && isAsset(selectedFile) ? (
            <div className="space-y-4">
              <div>
                <h2 className="text-sm font-semibold text-gray-900">{selectedFile.path}</h2>
                <p className="text-xs text-gray-500 mt-1">{selectedFile.content_type || "asset"} · {formatBytes(selectedFile.size_bytes)}</p>
              </div>
              {selectedFile.content_type?.startsWith("image/") && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={skillAssetUrl(name, selectedFile.path)} alt={selectedFile.path} className="max-w-full rounded-lg border border-gray-200" />
              )}
              {selectedFile.content_type === "application/pdf" && (
                <iframe src={skillAssetUrl(name, selectedFile.path)} className="w-full h-[620px] rounded-lg border border-gray-200" />
              )}
              <a href={skillAssetUrl(name, selectedFile.path)} target="_blank" rel="noreferrer" className="inline-flex px-3 py-2 text-sm font-semibold rounded-lg bg-gray-900 text-white">
                Open Asset
              </a>
            </div>
          ) : (
            <div className="space-y-3 h-full">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-semibold text-gray-900">{selectedPath}</h2>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-3 py-2 text-sm font-semibold rounded-lg bg-brand-500 text-gray-950 hover:bg-brand-400 disabled:opacity-50"
                >
                  {saving ? "Saving..." : "Save Live"}
                </button>
              </div>
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                className="w-full min-h-[620px] font-mono text-xs leading-relaxed border border-gray-200 rounded-lg p-4 bg-gray-50 focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-300"
              />
            </div>
          )}
        </div>

        <div className="bg-surface border border-gray-200 rounded-xl p-4">
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
        </div>
      </div>
    </div>
  );
}
