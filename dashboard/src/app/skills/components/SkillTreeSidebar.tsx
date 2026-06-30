"use client";

import Link from "next/link";
import type { Skill, SkillFile } from "../../../lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  RiAddLine,
  RiArrowRightSLine,
  RiChat1Line,
  RiGlobalLine,
  RiInformationLine,
  RiFileLine,
} from "@remixicon/react";

type ScopeKey = "workspace" | "personal" | "system";

const SECTIONS: { key: ScopeKey; label: string; showInfo?: boolean }[] = [
  { key: "workspace", label: "Workspace" },
  { key: "personal", label: "Personal" },
  { key: "system", label: "System", showInfo: true },
];

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
    <div className="w-[280px] flex-shrink-0 border-r border-border bg-muted/30 flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <h1 className="text-lg md:text-xl font-heading font-semibold text-foreground">Skills</h1>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-xs">
              <RiAddLine size={16} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem asChild>
              <Link href={createUrl}>
                <RiChat1Line size={16} className="text-muted-foreground" />
                Create skill in chat
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href={createUrl}>
                <RiGlobalLine size={16} className="text-muted-foreground" />
                Explore skills
              </Link>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Tree */}
      <ScrollArea className="flex-1">
        <div className="py-2">
          {SECTIONS.map(({ key, label, showInfo }) => {
            const sectionSkills = grouped[key];
            const isOpen = expandedSections.has(key);

            return (
              <div key={key} className="mb-1">
                <button
                  onClick={() => onToggleSection(key)}
                  className="w-full flex items-center gap-1.5 px-4 py-1.5 text-[11px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors"
                >
                  <RiArrowRightSLine
                    size={12}
                    className={cn("transition-transform duration-150", isOpen && "rotate-90")}
                  />
                  <span>{label}</span>
                  {showInfo && <RiInformationLine size={12} className="text-muted-foreground/50" />}
                </button>

                {isOpen && (
                  <div className="ml-2">
                    {sectionSkills.length === 0 ? (
                      <div className="px-4 py-1.5 text-xs text-muted-foreground">No skills</div>
                    ) : (
                      sectionSkills.map((skill) => {
                        const slug = skill.slug || skill.name;
                        const isSelected = selectedSkillSlug === slug;
                        const isSkillExpanded = expandedSkills.has(slug);
                        const files = skillFiles[slug] || skill.file_details || [];
                        const extraFiles = files.filter((f) => f.path !== "SKILL.md");
                        const hasExtraFiles = extraFiles.length > 0;

                        return (
                          <div key={slug}>
                            <div
                              className={cn(
                                "flex items-center gap-1 px-3 py-1.5 rounded-md mx-1 cursor-pointer transition-colors",
                                isSelected && !selectedFilePath
                                  ? "bg-accent text-accent-foreground"
                                  : "text-foreground/80 hover:bg-muted"
                              )}
                            >
                              {hasExtraFiles ? (
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    onToggleSkill(slug);
                                  }}
                                  className="p-0.5 flex-shrink-0"
                                >
                                  <RiArrowRightSLine
                                    size={14}
                                    className={cn(
                                      "text-muted-foreground transition-transform duration-150",
                                      isSkillExpanded && "rotate-90"
                                    )}
                                  />
                                </button>
                              ) : (
                                <span className="w-[18px] flex-shrink-0" />
                              )}
                              <button
                                onClick={() => onSelectSkill(slug)}
                                className="flex-1 text-left text-[13px] truncate"
                              >
                                {skill.name || slug}
                              </button>
                            </div>

                            {hasExtraFiles && isSkillExpanded && (
                              <div className="ml-6">
                                {files.map((file) => {
                                  const isFileSelected = isSelected && selectedFilePath === file.path;
                                  return (
                                    <button
                                      key={file.path}
                                      onClick={() => onSelectSkill(slug, file.path)}
                                      className={cn(
                                        "w-full flex items-center gap-1.5 px-3 py-1 rounded-md mx-1 text-left transition-colors",
                                        isFileSelected
                                          ? "bg-accent text-accent-foreground"
                                          : "text-muted-foreground hover:bg-muted hover:text-foreground"
                                      )}
                                    >
                                      <RiFileLine size={14} className="flex-shrink-0" />
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
      </ScrollArea>
    </div>
  );
}
