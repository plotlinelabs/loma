"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchAvailableTools,
  type AvailableTool,
  type AvailableSkill,
  type ToolConfig,
} from "@/lib/api";

export type ToolsLoadState = "idle" | "loading" | "ready" | "error";

export interface ToolsSelection {
  enabledSkills: string[] | null; // null = all
  enabledTools: string[] | null; // null = all
}

const ALWAYS_ENABLED_TOOLS = new Set(["Bash", "Read"]);

export function useToolsPicker(initial?: ToolConfig | null) {
  const [tools, setTools] = useState<AvailableTool[]>([]);
  const [skills, setSkills] = useState<AvailableSkill[]>([]);
  const [loadState, setLoadState] = useState<ToolsLoadState>("idle");
  const [selection, setSelection] = useState<ToolsSelection>({
    enabledSkills: initial?.enabled_skills ?? null,
    enabledTools: initial?.enabled_tools ?? null,
  });
  const fetchedRef = useRef(false);
  const toolsRef = useRef<AvailableTool[]>([]);
  const skillsRef = useRef<AvailableSkill[]>([]);

  // Keep refs in sync with state
  toolsRef.current = tools;
  skillsRef.current = skills;

  // Sync with external initial value (e.g. when loading an existing conversation)
  useEffect(() => {
    if (initial) {
      setSelection({
        enabledSkills: initial.enabled_skills ?? null,
        enabledTools: initial.enabled_tools ?? null,
      });
    }
  }, [initial]);

  const loadCatalog = useCallback(async () => {
    if (fetchedRef.current) return;
    fetchedRef.current = true;
    setLoadState("loading");
    try {
      const data = await fetchAvailableTools();
      setTools(data.tools);
      setSkills(data.skills);
      toolsRef.current = data.tools;
      skillsRef.current = data.skills;
      setLoadState("ready");
    } catch (e) {
      console.warn("Failed to load available tools", e);
      setLoadState("error");
    }
  }, []);

  const toggleTool = useCallback((toolId: string) => {
    if (ALWAYS_ENABLED_TOOLS.has(toolId)) return;
    setSelection((prev) => {
      if (prev.enabledTools === null) {
        const allIds = toolsRef.current.map((t) => t.id);
        return { ...prev, enabledTools: allIds.filter((id) => id !== toolId) };
      }
      const isEnabled = prev.enabledTools.includes(toolId);
      return {
        ...prev,
        enabledTools: isEnabled
          ? prev.enabledTools.filter((id) => id !== toolId)
          : [...prev.enabledTools, toolId],
      };
    });
  }, []);

  const toggleSkill = useCallback((slug: string) => {
    setSelection((prev) => {
      if (prev.enabledSkills === null) {
        const allSlugs = skillsRef.current.map((s) => s.slug);
        return { ...prev, enabledSkills: allSlugs.filter((s) => s !== slug) };
      }
      const isEnabled = prev.enabledSkills.includes(slug);
      return {
        ...prev,
        enabledSkills: isEnabled
          ? prev.enabledSkills.filter((s) => s !== slug)
          : [...prev.enabledSkills, slug],
      };
    });
  }, []);

  const enableAll = useCallback(() => {
    setSelection({ enabledSkills: null, enabledTools: null });
  }, []);

  const isAllEnabled = selection.enabledSkills === null && selection.enabledTools === null;

  const disabledCount = (() => {
    let count = 0;
    if (selection.enabledTools !== null) {
      count += tools.filter((t) => !selection.enabledTools!.includes(t.id)).length;
    }
    if (selection.enabledSkills !== null) {
      count += skills.filter((s) => !selection.enabledSkills!.includes(s.slug)).length;
    }
    return count;
  })();

  const toolConfig: ToolConfig | undefined =
    isAllEnabled ? undefined : {
      enabled_skills: selection.enabledSkills,
      enabled_tools: selection.enabledTools,
    };

  return {
    tools,
    skills,
    selection,
    loadState,
    loadCatalog,
    toggleTool,
    toggleSkill,
    enableAll,
    isAllEnabled,
    disabledCount,
    toolConfig,
    isAlwaysEnabled: (toolId: string) => ALWAYS_ENABLED_TOOLS.has(toolId),
  };
}
