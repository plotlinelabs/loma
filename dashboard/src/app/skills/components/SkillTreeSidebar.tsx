"use client";

import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import type { Skill, SkillFile } from "../../../lib/api";

type ScopeKey = "workspace" | "personal" | "system";

const SECTIONS: { key: ScopeKey; label: string; showInfo?: boolean }[] = [
  { key: "workspace", label: "Workspace" },
  { key: "personal", label: "Personal" },
  { key: "system", label: "System", showInfo: true },
];

function ChevronIcon({ open, className }: { open: boolean; className?: string }) {
  return (
    <svg
      className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-150 ${open ? "rotate-90" : ""} ${className || ""}`}
      fill="none"
      viewBox="0 0 24 24"
      strokeWidth={2}
      stroke="currentColor"
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
    </svg>
  );
}

export default function SkillTreeSidebar({
  skills,
  selectedSkillSlug,
  selectedFilePath,
  expandedSections,
  expandedSkills,
  skillFiles,
  onSelectSkill,
  onToggleSection,
  onToggleSkill,
  createUrl,
}: {
  skills: Skill[];
  selectedSkillSlug: string | null;
  selectedFilePath: string | null;
  expandedSections: Set<string>;
  expandedSkills: Set<string>;
  skillFiles: Record<string, SkillFile[]>;
  onSelectSkill: (slug: string, filePath?: string) => void;
  onToggleSection: (section: string) => void;
  onToggleSkill: (slug: string) => void;
  createUrl: string;
}) {
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!dropdownOpen) return;
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [dropdownOpen]);

  const grouped: Record<ScopeKey, Skill[]> = { workspace: [], personal: [], system: [] };
  for (const skill of skills) {
    const scope = skill.scope || "personal";
    if (scope in grouped) {
      grouped[scope as ScopeKey].push(skill);
    } else {
      grouped.personal.push(skill);
    }
  }

  return (
    <div className="w-[280px] flex-shrink-0 border-r border-gray-200 bg-gray-50/50 flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
        <h2 className="text-sm font-semibold text-gray-900">Skills</h2>
        <div className="relative" ref={dropdownRef}>
          <button
            onClick={() => setDropdownOpen(!dropdownOpen)}
            className="p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-200/60 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
          </button>
          {dropdownOpen && (
            <div className="absolute right-0 top-full mt-1 w-52 bg-surface border border-gray-200 rounded-lg shadow-lg z-20 py-1">
              <Link
                href={createUrl}
                onClick={() => setDropdownOpen(false)}
                className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
              >
                <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M7.5 8.25h9m-9 3H12m-9.75 1.51c0 1.6 1.123 2.994 2.707 3.227 1.129.166 2.27.293 3.423.379.35.026.67.21.865.501L12 21l2.755-4.133a1.14 1.14 0 0 1 .865-.501 48.172 48.172 0 0 0 3.423-.379c1.584-.233 2.707-1.626 2.707-3.228V6.741c0-1.602-1.123-2.995-2.707-3.228A48.394 48.394 0 0 0 12 3c-2.392 0-4.744.175-7.043.513C3.373 3.746 2.25 5.14 2.25 6.741v6.018Z" />
                </svg>
                Create skill in chat
              </Link>
              <Link
                href={createUrl}
                onClick={() => setDropdownOpen(false)}
                className="flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
              >
                <svg className="w-4 h-4 text-gray-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9.004 9.004 0 0 0 8.716-6.747M12 21a9.004 9.004 0 0 1-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 0 1 7.843 4.582M12 3a8.997 8.997 0 0 0-7.843 4.582m15.686 0A11.953 11.953 0 0 1 12 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0 1 21 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0 1 12 16.5a17.92 17.92 0 0 1-8.716-2.247m0 0A9.015 9.015 0 0 1 3 12c0-1.605.42-3.113 1.157-4.418" />
                </svg>
                Explore skills
              </Link>
            </div>
          )}
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-2">
        {SECTIONS.map(({ key, label, showInfo }) => {
          const sectionSkills = grouped[key];
          const isOpen = expandedSections.has(key);

          return (
            <div key={key} className="mb-1">
              {/* Section header */}
              <button
                onClick={() => onToggleSection(key)}
                className="w-full flex items-center gap-1.5 px-4 py-1.5 text-[11px] font-semibold text-gray-400 uppercase tracking-wider hover:text-gray-600 transition-colors"
              >
                <ChevronIcon open={isOpen} className="!w-3 !h-3" />
                <span>{label}</span>
                {showInfo && (
                  <svg className="w-3 h-3 text-gray-300 ml-0.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z" />
                  </svg>
                )}
              </button>

              {isOpen && (
                <div className="ml-2">
                  {sectionSkills.length === 0 ? (
                    <div className="px-4 py-1.5 text-xs text-gray-400">No skills</div>
                  ) : (
                    sectionSkills.map((skill) => {
                      const slug = skill.slug || skill.name;
                      const isSelected = selectedSkillSlug === slug;
                      const isSkillExpanded = expandedSkills.has(slug);
                      const files = skillFiles[slug] || skill.file_details || [];

                      return (
                        <div key={slug}>
                          {/* Skill row */}
                          <div
                            className={`flex items-center gap-1 px-3 py-1.5 rounded-md mx-1 cursor-pointer transition-colors ${
                              isSelected && !selectedFilePath
                                ? "bg-brand-100/80 text-brand-700"
                                : "text-gray-700 hover:bg-gray-200/50"
                            }`}
                          >
                            <button
                              onClick={(e) => {
                                e.stopPropagation();
                                onToggleSkill(slug);
                              }}
                              className="p-0.5 flex-shrink-0"
                            >
                              <ChevronIcon open={isSkillExpanded} />
                            </button>
                            <button
                              onClick={() => onSelectSkill(slug)}
                              className="flex-1 text-left text-[13px] font-medium truncate"
                            >
                              {skill.name || slug}
                            </button>
                          </div>

                          {/* File sub-tree */}
                          {isSkillExpanded && (
                            <div className="ml-6">
                              {files.map((file) => {
                                const isFileSelected = isSelected && selectedFilePath === file.path;
                                return (
                                  <button
                                    key={file.path}
                                    onClick={() => onSelectSkill(slug, file.path)}
                                    className={`w-full flex items-center gap-1.5 px-3 py-1 rounded-md mx-1 text-left transition-colors ${
                                      isFileSelected
                                        ? "bg-brand-100/80 text-brand-700"
                                        : "text-gray-500 hover:bg-gray-200/50 hover:text-gray-700"
                                    }`}
                                  >
                                    <FileIcon />
                                    <span className="text-xs truncate">{file.path}</span>
                                  </button>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
