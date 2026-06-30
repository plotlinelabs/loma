"use client";

import Link from "next/link";
import type { Skill, SkillFile } from "../../../lib/api";
import { updateSkillScope } from "../../../lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  RiAddLine,
  RiArrowRightSLine,
  RiChat1Line,
  RiGlobalLine,
  RiInformationLine,
  RiFileLine,
  RiGroupLine,
  RiUserLine,
  RiMoreLine,
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
  width,
  onSkillsChanged,
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
  width?: number;
  onSkillsChanged?: () => void;
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

  async function handleMoveScope(slug: string, newScope: "personal" | "workspace") {
    try {
      await updateSkillScope(slug, newScope);
      onSkillsChanged?.();
    } catch {
      // silent
    }
  }

  return (
    <div className="flex-shrink-0 bg-muted/30 flex flex-col h-full overflow-hidden" style={{ width: width ?? 280 }}>
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
                New
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem disabled>
              <RiGlobalLine size={16} className="text-muted-foreground" />
              Explore
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
                        const isMovable = key !== "system";

                        return (
                          <div key={slug}>
                            <div
                              className={cn(
                                "group flex items-center gap-1 px-3 py-1.5 rounded-md mx-1 cursor-pointer transition-colors",
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
                              {isMovable && (
                                <DropdownMenu>
                                  <DropdownMenuTrigger asChild>
                                    <button
                                      onClick={(e) => e.stopPropagation()}
                                      className="p-0.5 rounded flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
                                    >
                                      <RiMoreLine size={14} />
                                    </button>
                                  </DropdownMenuTrigger>
                                  <DropdownMenuContent align="end" className="min-w-[160px]">
                                    {key === "personal" ? (
                                      <DropdownMenuItem onClick={() => handleMoveScope(slug, "workspace")}>
                                        <RiGroupLine size={14} className="text-muted-foreground" />
                                        Move to Workspace
                                      </DropdownMenuItem>
                                    ) : (
                                      <DropdownMenuItem onClick={() => handleMoveScope(slug, "personal")}>
                                        <RiUserLine size={14} className="text-muted-foreground" />
                                        Move to Personal
                                      </DropdownMenuItem>
                                    )}
                                  </DropdownMenuContent>
                                </DropdownMenu>
                              )}
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
