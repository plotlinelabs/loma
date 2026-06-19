"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchSkills, basePath } from "../../lib/api";
import type { Skill } from "../../lib/api";

const SKILL_COLORS = [
  "bg-blue-100 text-blue-700",
  "bg-purple-100 text-purple-700",
  "bg-green-100 text-green-700",
  "bg-orange-100 text-orange-700",
  "bg-slate-100 text-slate-700",
  "bg-rose-100 text-rose-700",
  "bg-teal-100 text-teal-700",
  "bg-amber-100 text-amber-700",
];

function formatSkillName(name: string): string {
  return name.split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
}

function SkeletonRow() {
  return (
    <tr>
      <td className="px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="skeleton w-2.5 h-2.5 rounded-full" />
          <div className="skeleton h-3.5 w-28" />
        </div>
      </td>
      <td className="px-5 py-4"><div className="skeleton h-3 w-64" /></td>
      <td className="px-5 py-4">
        <div className="flex gap-1.5">
          <div className="skeleton h-5 w-16 rounded-md" />
          <div className="skeleton h-5 w-20 rounded-md" />
        </div>
      </td>
    </tr>
  );
}

function MobileSkeletonCard() {
  return (
    <div className="bg-surface rounded-xl border border-gray-200 p-4">
      <div className="flex items-center gap-2 mb-2">
        <div className="skeleton w-2.5 h-2.5 rounded-full" />
        <div className="skeleton h-4 w-28" />
      </div>
      <div className="skeleton h-3 w-full mb-2" />
      <div className="flex gap-1.5">
        <div className="skeleton h-5 w-16 rounded-md" />
        <div className="skeleton h-5 w-20 rounded-md" />
      </div>
    </div>
  );
}

export default function SkillsPage() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchSkills()
      .then((data) => setSkills(data.skills))
      .catch(() => setSkills([]))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="space-y-4 md:space-y-6 animate-fade-in-up">
        <div>
          <h1 className="text-lg md:text-xl font-semibold text-gray-900">Agent Skills</h1>
          <p className="text-sm text-gray-500 mt-1">Loading skills...</p>
        </div>
        {/* Desktop skeleton */}
        <div className="desktop-table bg-surface border border-gray-200 rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50/50">
                <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Skill</th>
                <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Description</th>
                <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Extra Files</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              <SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow />
            </tbody>
          </table>
        </div>
        {/* Mobile skeleton */}
        <div className="mobile-cards space-y-3">
          <MobileSkeletonCard /><MobileSkeletonCard /><MobileSkeletonCard /><MobileSkeletonCard />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4 md:space-y-6 animate-fade-in-up">
      <div>
        <h1 className="text-lg md:text-xl font-semibold text-gray-900">Agent Skills</h1>
        <p className="text-sm text-gray-500 mt-1">
          Domain-specific playbooks loaded on-demand by the agent. {skills.length} skill{skills.length !== 1 ? "s" : ""} available.
        </p>
      </div>

      {/* Desktop table */}
      <div className="desktop-table bg-surface border border-gray-200 rounded-xl overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50/50">
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Skill</th>
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Description</th>
              <th className="text-left text-xs font-semibold text-gray-500 uppercase tracking-wider px-5 py-3">Extra Files</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 stagger-children">
            {skills.map((skill, idx) => {
              const color = SKILL_COLORS[idx % SKILL_COLORS.length];
              return (
                <tr key={skill.name} className="hover:bg-gray-50 transition-colors">
                  <td className="px-5 py-4">
                    <Link href={`/skills/${skill.name}`} className="flex items-center gap-3 group">
                      <span className={`inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${color.split(" ")[0]}`} />
                      <span className="text-sm font-medium text-gray-900 group-hover:text-brand-700 transition-colors">{formatSkillName(skill.name)}</span>
                    </Link>
                  </td>
                  <td className="px-5 py-4"><span className="text-sm text-gray-500">{skill.description}</span></td>
                  <td className="px-5 py-4">
                    {skill.files.length > 0 ? (
                      <div className="flex flex-wrap gap-1.5">
                        {skill.files.map((file) => (
                          <span key={file} className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-md font-mono">{file}</span>
                        ))}
                      </div>
                    ) : <span className="text-xs text-gray-400">-</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {skills.length === 0 && (
          <div className="text-center py-12 text-gray-400 text-sm">
            No skills found. Add skill directories under <code className="bg-gray-100 px-1.5 py-0.5 rounded">.claude/skills/</code>
          </div>
        )}
      </div>

      {/* Mobile card list */}
      <div className="mobile-cards space-y-3 stagger-children">
        {skills.map((skill, idx) => {
          const color = SKILL_COLORS[idx % SKILL_COLORS.length];
          return (
            <Link key={skill.name} href={`/skills/${skill.name}`} className="block bg-surface border border-gray-200 rounded-xl p-4 active:bg-gray-50 transition-colors">
              <div className="flex items-center gap-2 mb-1">
                <span className={`inline-block w-2.5 h-2.5 rounded-full flex-shrink-0 ${color.split(" ")[0]}`} />
                <span className="text-sm font-semibold text-gray-900">{formatSkillName(skill.name)}</span>
              </div>
              <p className="text-xs text-gray-500 mb-2">{skill.description}</p>
              {skill.files.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {skill.files.map((file) => (
                    <span key={file} className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-md font-mono">{file}</span>
                  ))}
                </div>
              )}
            </Link>
          );
        })}
        {skills.length === 0 && (
          <div className="bg-surface border border-gray-200 rounded-xl p-8 text-center text-gray-400 text-sm">
            No skills found.
          </div>
        )}
      </div>
    </div>
  );
}
