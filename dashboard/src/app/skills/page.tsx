"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { basePath, fetchSkills } from "../../lib/api";
import type { Skill } from "../../lib/api";

function chatUrl(prompt: string): string {
  return `${basePath}/chat?prompt=${encodeURIComponent(prompt)}`;
}

function buildCreateSkillPrompt(): string {
  return [
    "I want to create a new Loma skill.",
    "",
    "Please help me design the skill first, then create it using `python3 tools/loma_skills.py create` only after I confirm the slug, description, and SKILL.md content.",
    "",
    "Ask me for:",
    "- skill slug",
    "- when the agent should use it",
    "- required steps or playbook content",
    "- supporting files or assets, if any",
  ].join("\n");
}

function formatUpdated(value?: string): string {
  if (!value) return "-";
  return new Date(value).toLocaleString();
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const createPromptUrl = useMemo(() => chatUrl(buildCreateSkillPrompt()), []);

  useEffect(() => {
    setLoading(true);
    fetchSkills()
      .then((data) => setSkills(data.skills))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load skills"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="space-y-5 animate-fade-in-up">
      <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4">
        <div>
          <h1 className="text-lg md:text-xl font-semibold text-gray-900">Skills</h1>
          <p className="text-sm text-gray-500 mt-1">
            Company playbooks and supporting files the agent can search, read, and update through chat.
          </p>
        </div>
        <Link
          href={createPromptUrl}
          className="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold rounded-lg bg-brand-500 text-gray-950 hover:bg-brand-400 transition-colors"
        >
          Create Skill in Chat
        </Link>
      </div>

      {error && (
        <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3">
          {error}
        </div>
      )}

      <div className="bg-surface border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50/50">
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Skill</th>
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Description</th>
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Tags</th>
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Files</th>
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr>
                <td colSpan={5} className="px-5 py-10 text-center text-sm text-gray-400">Loading skills...</td>
              </tr>
            ) : skills.length === 0 ? (
              <tr>
                <td colSpan={5} className="px-5 py-10 text-center text-sm text-gray-400">
                  No skills yet. Create one in chat to make company knowledge available to the agent.
                </td>
              </tr>
            ) : (
              skills.map((skill) => {
                const slug = skill.slug || skill.name;
                const files = skill.file_details || [];
                const assetCount = files.filter((file) => file.kind === "local_asset").length;
                const textCount = files.filter((file) => file.kind === "inline_text").length;
                return (
                  <tr key={slug} className="hover:bg-gray-50 transition-colors">
                    <td className="px-5 py-4 align-top">
                      <Link href={`/skills/${slug}`} className="text-sm font-semibold text-gray-900 hover:text-brand-700">
                        {skill.name || slug}
                      </Link>
                      <div className="text-xs text-gray-400 font-mono mt-1">{slug}</div>
                    </td>
                    <td className="px-5 py-4 align-top text-sm text-gray-500 max-w-xl">{skill.description || "-"}</td>
                    <td className="px-5 py-4 align-top">
                      {skill.tags?.length ? (
                        <div className="flex flex-wrap gap-1.5">
                          {skill.tags.map((tag) => (
                            <span key={tag} className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-md">{tag}</span>
                          ))}
                        </div>
                      ) : <span className="text-xs text-gray-400">-</span>}
                    </td>
                    <td className="px-5 py-4 align-top text-xs text-gray-500">
                      {textCount} text · {assetCount} asset{assetCount === 1 ? "" : "s"}
                    </td>
                    <td className="px-5 py-4 align-top text-xs text-gray-400">{formatUpdated(skill.updated_at)}</td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
