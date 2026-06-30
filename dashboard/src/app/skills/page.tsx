"use client";

import { useEffect, useState, useMemo, useCallback, useRef, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { basePath, fetchSkills, fetchSkill } from "../../lib/api";
import type { Skill, SkillDetailResponse, SkillFile } from "../../lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import SkillTreeSidebar from "./components/SkillTreeSidebar";
import SkillDetailPane from "./components/SkillDetailPane";

const DEFAULT_SIDEBAR_WIDTH = 280;
const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 480;

function PanelResizer({ onResize, onDoubleClick }: { onResize: (delta: number) => void; onDoubleClick: () => void }) {
  const isDragging = useRef(false);
  const startX = useRef(0);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    isDragging.current = true;
    startX.current = e.clientX;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const handleMouseMove = (moveEvent: MouseEvent) => {
      if (!isDragging.current) return;
      const delta = moveEvent.clientX - startX.current;
      startX.current = moveEvent.clientX;
      onResize(delta);
    };

    const handleMouseUp = () => {
      isDragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };

    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
  }, [onResize]);

  return (
    <div
      className="w-1 hover:w-1.5 bg-border hover:bg-accent-foreground/20 cursor-col-resize flex-shrink-0 transition-all duration-100 relative group"
      onMouseDown={handleMouseDown}
      onDoubleClick={onDoubleClick}
      title="Drag to resize. Double-click to reset."
    >
      <div className="absolute inset-y-0 -left-1 -right-1" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity">
        <div className="flex flex-col gap-1">
          <div className="w-1 h-1 rounded-full bg-muted-foreground" />
          <div className="w-1 h-1 rounded-full bg-muted-foreground" />
          <div className="w-1 h-1 rounded-full bg-muted-foreground" />
        </div>
      </div>
    </div>
  );
}

function buildCreateSkillUrl(): string {
  const prompt = "I want to create a new Loma skill. Help me design it, then create it using `python3 tools/loma_skills.py create` after I confirm.";
  const params = new URLSearchParams({ prompt, autoSend: "true" });
  return `${basePath}/chat?${params.toString()}`;
}

function SkillsPageInner() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const selectedSkillSlug = searchParams.get("skill");
  const selectedFilePath = searchParams.get("file");

  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillDetail, setSkillDetail] = useState<SkillDetailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(
    new Set(["workspace", "personal", "system"])
  );
  const [expandedSkills, setExpandedSkills] = useState<Set<string>>(new Set());
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH);

  const handleResize = useCallback((delta: number) => {
    setSidebarWidth((prev) => Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, prev + delta)));
  }, []);

  const handleResetSplit = useCallback(() => {
    setSidebarWidth(DEFAULT_SIDEBAR_WIDTH);
  }, []);

  const createUrl = useMemo(() => buildCreateSkillUrl(), []);

  const skillFiles: Record<string, SkillFile[]> = useMemo(() => {
    const map: Record<string, SkillFile[]> = {};
    for (const skill of skills) {
      const slug = skill.slug || skill.name;
      map[slug] = skill.file_details || [];
    }
    return map;
  }, [skills]);

  const loadSkills = useCallback(() => {
    fetchSkills()
      .then((data) => setSkills(data.skills))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    setLoading(true);
    loadSkills();
  }, [loadSkills]);

  useEffect(() => {
    if (!selectedSkillSlug) {
      setSkillDetail(null);
      return;
    }
    setDetailLoading(true);
    fetchSkill(selectedSkillSlug)
      .then(setSkillDetail)
      .catch(() => setSkillDetail(null))
      .finally(() => setDetailLoading(false));
  }, [selectedSkillSlug]);

  useEffect(() => {
    if (selectedSkillSlug) {
      const skill = skills.find((s) => (s.slug || s.name) === selectedSkillSlug);
      if (skill?.scope) {
        setExpandedSections((prev) => new Set([...prev, skill.scope!]));
      }
      setExpandedSkills((prev) => new Set([...prev, selectedSkillSlug]));
    }
  }, [selectedSkillSlug, skills]);

  function updateUrl(skill: string | null, file?: string | null) {
    const params = new URLSearchParams();
    if (skill) params.set("skill", skill);
    if (file) params.set("file", file);
    const qs = params.toString();
    router.replace(`/skills${qs ? `?${qs}` : ""}`, { scroll: false });
  }

  function handleSelectSkill(slug: string, filePath?: string) {
    updateUrl(slug, filePath || null);
  }

  function handleToggleSection(section: string) {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  }

  function handleToggleSkill(slug: string) {
    setExpandedSkills((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function handleSkillUpdated() {
    if (selectedSkillSlug) {
      fetchSkill(selectedSkillSlug)
        .then(setSkillDetail)
        .catch(() => {});
    }
    loadSkills();
  }

  if (loading) {
    return (
      <div className="flex h-full">
        <div className="w-[280px] flex-shrink-0 border-r border-border bg-muted/50 p-4 space-y-3">
          <Skeleton className="h-4 w-20" />
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-3 w-28" />
          <Skeleton className="h-3 w-36" />
        </div>
        <div className="flex-1 flex items-center justify-center">
          <p className="text-[13px] text-muted-foreground">Loading skills...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full">
      <SkillTreeSidebar
        skills={skills}
        selectedSkillSlug={selectedSkillSlug}
        selectedFilePath={selectedFilePath}
        expandedSections={expandedSections}
        expandedSkills={expandedSkills}
        skillFiles={skillFiles}
        onSelectSkill={handleSelectSkill}
        onToggleSection={handleToggleSection}
        onToggleSkill={handleToggleSkill}
        createUrl={createUrl}
        width={sidebarWidth}
        onSkillsChanged={handleSkillUpdated}
      />
      <PanelResizer onResize={handleResize} onDoubleClick={handleResetSplit} />
      <SkillDetailPane
        skill={skillDetail}
        selectedFilePath={selectedFilePath}
        loading={detailLoading}
        createUrl={createUrl}
        onSkillUpdated={handleSkillUpdated}
      />
    </div>
  );
}

export default function SkillsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full">
          <div className="w-[280px] flex-shrink-0 border-r border-border bg-muted/50 animate-pulse" />
          <div className="flex-1 flex items-center justify-center">
            <p className="text-[13px] text-muted-foreground">Loading skills...</p>
          </div>
        </div>
      }
    >
      <SkillsPageInner />
    </Suspense>
  );
}
