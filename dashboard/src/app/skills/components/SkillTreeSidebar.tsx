"use client";

import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import type { Skill, SkillFile } from "../../../lib/api";
import { updateSkillScope, updateSkillFolder, deleteSkill, autoOrganizeSkills } from "../../../lib/api";
import { useUser } from "../../../lib/UserContext";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
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
  RiFolderLine,
  RiFolderOpenLine,
  RiDeleteBinLine,
  RiSparklingLine,
  RiCloseLine,
  RiCheckLine,
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
  const { user, isAdmin, hasRole } = useUser();
  const userEmail = user?.email || "";
  const isMaintainer = hasRole("maintainer");

  const [showNewFolder, setShowNewFolder] = useState<string | null>(null);
  const [newFolderName, setNewFolderName] = useState("");
  const [organizing, setOrganizing] = useState(false);
  const folderInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (showNewFolder && folderInputRef.current) {
      folderInputRef.current.focus();
    }
  }, [showNewFolder]);

  // Group skills: Scope → Folder → Skills
  const allFolders = new Set<string>();
  const grouped: Record<ScopeKey, { ungrouped: Skill[]; folders: Map<string, Skill[]> }> = {
    workspace: { ungrouped: [], folders: new Map() },
    personal: { ungrouped: [], folders: new Map() },
    system: { ungrouped: [], folders: new Map() },
  };

  for (const skill of skills) {
    const scope = (skill.scope || "personal") as ScopeKey;
    const target = grouped[scope] || grouped.personal;
    if (skill.folder) {
      allFolders.add(skill.folder);
      if (!target.folders.has(skill.folder)) target.folders.set(skill.folder, []);
      target.folders.get(skill.folder)!.push(skill);
    } else {
      target.ungrouped.push(skill);
    }
  }

  async function handleMoveScope(slug: string, newScope: "personal" | "workspace") {
    try {
      await updateSkillScope(slug, newScope);
      onSkillsChanged?.();
    } catch {}
  }

  async function handleMoveFolder(slug: string, folder: string | null) {
    try {
      await updateSkillFolder(slug, folder);
      onSkillsChanged?.();
    } catch {}
  }

  async function handleDelete(slug: string, name: string) {
    if (!confirm(`Delete skill "${name}"? This cannot be undone.`)) return;
    try {
      await deleteSkill(slug);
      onSkillsChanged?.();
    } catch {}
  }

  async function handleAutoOrganize() {
    setOrganizing(true);
    try {
      await autoOrganizeSkills();
      onSkillsChanged?.();
    } catch {} finally {
      setOrganizing(false);
    }
  }

  function handleCreateFolder(slug: string) {
    const trimmed = newFolderName.trim();
    if (trimmed) {
      handleMoveFolder(slug, trimmed);
    }
    setShowNewFolder(null);
    setNewFolderName("");
  }

  function canDelete(skill: Skill, scopeKey: ScopeKey): boolean {
    if (scopeKey === "system") return false;
    if (scopeKey === "workspace") return isAdmin;
    return skill.created_by === userEmail;
  }

  function renderSkillRow(skill: Skill, scopeKey: ScopeKey) {
    const slug = skill.slug || skill.name;
    const isSelected = selectedSkillSlug === slug;
    const isSkillExpanded = expandedSkills.has(slug);
    const files = skillFiles[slug] || skill.file_details || [];
    const extraFiles = files.filter((f) => f.path !== "SKILL.md");
    const hasExtraFiles = extraFiles.length > 0;
    const isMovable = scopeKey !== "system";
    const deletable = canDelete(skill, scopeKey);

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
              onClick={(e) => { e.stopPropagation(); onToggleSkill(slug); }}
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
          {(isMovable || deletable) && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  onClick={(e) => e.stopPropagation()}
                  className="p-0.5 rounded flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
                >
                  <RiMoreLine size={14} />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="min-w-[180px]">
                {isMovable && (
                  <>
                    {scopeKey === "personal" ? (
                      <DropdownMenuItem onClick={() => handleMoveScope(slug, "workspace")}>
                        <RiGroupLine size={14} className="text-muted-foreground" />
                        Move to Workspace
                      </DropdownMenuItem>
                    ) : scopeKey === "workspace" ? (
                      <DropdownMenuItem onClick={() => handleMoveScope(slug, "personal")}>
                        <RiUserLine size={14} className="text-muted-foreground" />
                        Move to Personal
                      </DropdownMenuItem>
                    ) : null}

                    <DropdownMenuSub>
                      <DropdownMenuSubTrigger onClick={(e) => e.stopPropagation()}>
                        <RiFolderLine size={14} className="text-muted-foreground" />
                        {skill.folder ? "Change folder" : "Move to folder"}
                      </DropdownMenuSubTrigger>
                      <DropdownMenuSubContent className="w-48">
                        {skill.folder && (
                          <DropdownMenuItem onClick={() => handleMoveFolder(slug, null)}>
                            <RiCloseLine size={14} />
                            Remove from folder
                          </DropdownMenuItem>
                        )}
                        {[...allFolders].sort().map((f) => (
                          <DropdownMenuItem
                            key={f}
                            onClick={() => handleMoveFolder(slug, f)}
                            className={f === skill.folder ? "text-brand-600 font-medium" : ""}
                          >
                            <RiFolderLine size={14} className="text-muted-foreground" />
                            <span className="truncate">{f}</span>
                            {f === skill.folder && (
                              <RiCheckLine size={14} className="text-brand-600 ml-auto flex-shrink-0" />
                            )}
                          </DropdownMenuItem>
                        ))}
                        {showNewFolder === slug ? (
                          <div className="px-2 py-1.5">
                            <Input
                              ref={folderInputRef}
                              type="text"
                              value={newFolderName}
                              onChange={(e) => setNewFolderName(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") handleCreateFolder(slug);
                                if (e.key === "Escape") { setShowNewFolder(null); setNewFolderName(""); }
                              }}
                              placeholder="Folder name..."
                              className="h-7 text-xs"
                            />
                          </div>
                        ) : (
                          <DropdownMenuItem onClick={(e) => { e.preventDefault(); setShowNewFolder(slug); setNewFolderName(""); }}>
                            <RiAddLine size={14} className="text-muted-foreground" />
                            New folder
                          </DropdownMenuItem>
                        )}
                      </DropdownMenuSubContent>
                    </DropdownMenuSub>
                  </>
                )}
                {deletable && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      variant="destructive"
                      onClick={() => handleDelete(slug, skill.name || slug)}
                    >
                      <RiDeleteBinLine size={14} />
                      Delete
                    </DropdownMenuItem>
                  </>
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
  }

  return (
    <div className="flex-shrink-0 bg-muted/30 flex flex-col h-full overflow-hidden" style={{ width: width ?? 280 }}>
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
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
            {isMaintainer && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleAutoOrganize} disabled={organizing}>
                  <RiSparklingLine size={16} className="text-muted-foreground" />
                  {organizing ? "Organizing..." : "Auto-organize"}
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {/* Tree */}
      <ScrollArea className="flex-1 min-h-0">
        <div className="py-2">
          {SECTIONS.map(({ key, label, showInfo }) => {
            const { ungrouped, folders } = grouped[key];
            const totalCount = ungrouped.length + [...folders.values()].reduce((n, s) => n + s.length, 0);
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
                  <span className="text-muted-foreground/50 font-normal normal-case">({totalCount})</span>
                  {showInfo && <RiInformationLine size={12} className="text-muted-foreground/50" />}
                </button>

                {isOpen && (
                  <div className="ml-2">
                    {totalCount === 0 ? (
                      <div className="px-4 py-1.5 text-xs text-muted-foreground">No skills</div>
                    ) : (
                      <>
                        {/* Folders */}
                        {[...folders.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([folderName, folderSkills]) => {
                          const folderKey = `folder:${key}:${folderName}`;
                          const isFolderOpen = expandedSections.has(folderKey);
                          return (
                            <div key={folderKey}>
                              <button
                                onClick={() => onToggleSection(folderKey)}
                                className="w-full flex items-center gap-1.5 px-3 py-1.5 rounded-md mx-1 text-[12px] text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
                              >
                                {isFolderOpen ? (
                                  <RiFolderOpenLine size={14} className="flex-shrink-0 text-amber-500" />
                                ) : (
                                  <RiFolderLine size={14} className="flex-shrink-0 text-amber-500" />
                                )}
                                <span className="truncate font-medium">{folderName}</span>
                                <span className="text-muted-foreground/50 text-[11px] ml-auto">{folderSkills.length}</span>
                              </button>
                              {isFolderOpen && (
                                <div className="ml-3">
                                  {folderSkills.map((skill) => renderSkillRow(skill, key))}
                                </div>
                              )}
                            </div>
                          );
                        })}
                        {/* Ungrouped skills */}
                        {ungrouped.map((skill) => renderSkillRow(skill, key))}
                      </>
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
