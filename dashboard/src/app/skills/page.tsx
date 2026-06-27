"use client";

import { useEffect, useState, useMemo, useCallback, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { basePath, fetchSkills, fetchSkill } from "../../lib/api";
import type { Skill, SkillDetailResponse, SkillFile } from "../../lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import SkillTreeSidebar from "./components/SkillTreeSidebar";
import SkillDetailPane from "./components/SkillDetailPane";

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

  const createUrl = useMemo(() => chatUrl(buildCreateSkillPrompt()), []);

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

  function handleNavigate(level: "root" | "skill") {
    if (level === "root") {
      updateUrl(null);
    } else if (level === "skill") {
      updateUrl(selectedSkillSlug, null);
    }
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
      />
      <SkillDetailPane
        skill={skillDetail}
        selectedFilePath={selectedFilePath}
        loading={detailLoading}
        createUrl={createUrl}
        onNavigate={handleNavigate}
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
