"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createSkill, fetchSkills } from "../../lib/api";
import type { Skill } from "../../lib/api";

function templateSkill(slug: string): string {
  const name = slug
    .split("-")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ") || "New Skill";
  return `---\nname: ${name}\ndescription: Describe when the agent should use this skill.\ntags: []\n---\n\n# ${name}\n\nUse this skill when...\n\n## Steps\n\n1. \n`;
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [slug, setSlug] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    fetchSkills()
      .then((data) => setSkills(data.skills))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load skills"))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const handleCreate = async () => {
    const cleanSlug = slug.trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
    if (!cleanSlug) {
      setError("Enter a skill slug.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      await createSkill({ slug: cleanSlug, content: templateSkill(cleanSlug) });
      setSlug("");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create skill");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="space-y-5 animate-fade-in-up">
      <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4">
        <div>
          <h1 className="text-lg md:text-xl font-semibold text-gray-900">Agent Skills</h1>
          <p className="text-sm text-gray-500 mt-1">
            DB-backed playbooks the agent can search, read, and update through Loma tools.
          </p>
        </div>
        <div className="flex gap-2 w-full lg:w-auto">
          <input
            value={slug}
            onChange={(e) => setSlug(e.target.value)}
            placeholder="new-skill-slug"
            className="flex-1 lg:w-64 px-3 py-2 text-sm border border-gray-200 rounded-lg bg-surface focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-300"
          />
          <button
            onClick={handleCreate}
            disabled={creating}
            className="px-4 py-2 text-sm font-semibold rounded-lg bg-brand-500 text-gray-950 hover:bg-brand-400 disabled:opacity-50"
          >
            {creating ? "Creating..." : "New Skill"}
          </button>
        </div>
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
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Files</th>
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {loading ? (
              <tr>
                <td colSpan={4} className="px-5 py-10 text-center text-sm text-gray-400">Loading skills...</td>
              </tr>
            ) : skills.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-5 py-10 text-center text-sm text-gray-400">
                  No skills yet. Create one to make company knowledge available to the agent.
                </td>
              </tr>
            ) : (
              skills.map((skill) => {
                const slug = skill.slug || skill.name;
                return (
                  <tr key={slug} className="hover:bg-gray-50 transition-colors">
                    <td className="px-5 py-4">
                      <Link href={`/skills/${slug}`} className="text-sm font-semibold text-gray-900 hover:text-brand-700">
                        {slug}
                      </Link>
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-500 max-w-xl">{skill.description || "-"}</td>
                    <td className="px-5 py-4">
                      <span className="text-xs text-gray-500">{skill.file_details?.length ?? skill.files.length + 1} file(s)</span>
                    </td>
                    <td className="px-5 py-4 text-xs text-gray-400">
                      {skill.updated_at ? new Date(skill.updated_at).toLocaleString() : "-"}
                    </td>
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
