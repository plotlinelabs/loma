"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAvailableTools,
  type AvailableTool,
  type AvailableSkill,
  type ToolConfig,
} from "@/lib/api";
import type { AgentIdentity } from "@/lib/agents-api";

export type ToolsLoadState = "idle" | "loading" | "ready" | "error";

export interface ToolsSelection {
  enabledSkills: string[] | null; // null = all
  enabledTools: string[] | null; // null = all
}

const ALWAYS_ENABLED_TOOLS = new Set(["Bash", "Read"]);

export function useToolsPicker(
  initial?: ToolConfig | null,
  selectedAgent?: AgentIdentity | null,
) {
  const [allTools, setAllTools] = useState<AvailableTool[]>([]);
  const [allSkills, setAllSkills] = useState<AvailableSkill[]>([]);
  const [loadState, setLoadState] = useState<ToolsLoadState>("idle");
  const [selection, setSelection] = useState<ToolsSelection>({
    enabledSkills: initial?.enabled_skills ?? null,
    enabledTools: initial?.enabled_tools ?? null,
  });
  const fetchedRef = useRef(false);
  const allToolsRef = useRef<AvailableTool[]>([]);
  const allSkillsRef = useRef<AvailableSkill[]>([]);

  allToolsRef.current = allTools;
  allSkillsRef.current = allSkills;

  useEffect(() => {
    if (initial) {
      setSelection({
        enabledSkills: initial.enabled_skills ?? null,
        enabledTools: initial.enabled_tools ?? null,
      });
    }
  }, [initial]);

  // Reset selection when agent changes
  useEffect(() => {
    setSelection({ enabledSkills: null, enabledTools: null });
  }, [selectedAgent?.agent_id]);

  const loadCatalog = useCallback(async () => {
    if (fetchedRef.current) return;
    fetchedRef.current = true;
    setLoadState("loading");
    try {
      const data = await fetchAvailableTools();
      setAllTools(data.tools);
      setAllSkills(data.skills);
      allToolsRef.current = data.tools;
      allSkillsRef.current = data.skills;
      setLoadState("ready");
    } catch (e) {
      console.warn("Failed to load available tools", e);
      setLoadState("error");
    }
  }, []);

  // Scope tools/skills to the selected agent's allowed set
  const tools = useMemo(() => {
    if (!selectedAgent || selectedAgent.tools.length === 0) return allTools;
    const agentToolSet = new Set(selectedAgent.tools);
    return allTools.filter((t) => agentToolSet.has(t.id));
  }, [allTools, selectedAgent]);

  const skills = useMemo(() => {
    if (!selectedAgent || selectedAgent.skills.length === 0) return allSkills;
    const agentSkillSet = new Set(selectedAgent.skills);
    return allSkills.filter((s) => agentSkillSet.has(s.slug));
  }, [allSkills, selectedAgent]);

  const toggleTool = useCallback((toolId: string) => {
    if (ALWAYS_ENABLED_TOOLS.has(toolId)) return;
    setSelection((prev) => {
      if (prev.enabledTools === null) {
        const allIds = allToolsRef.current.map((t) => t.id);
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
        const slugs = allSkillsRef.current.map((s) => s.slug);
        return { ...prev, enabledSkills: slugs.filter((s) => s !== slug) };
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

  const enableAllTools = useCallback(() => {
    setSelection((prev) => ({ ...prev, enabledTools: null }));
  }, []);

  const enableAllSkills = useCallback(() => {
    setSelection((prev) => ({ ...prev, enabledSkills: null }));
  }, []);

  const isAllToolsEnabled = selection.enabledTools === null;
  const isAllSkillsEnabled = selection.enabledSkills === null;

  const disabledToolsCount = selection.enabledTools === null
    ? 0
    : tools.filter((t) => !selection.enabledTools!.includes(t.id)).length;

  const disabledSkillsCount = selection.enabledSkills === null
    ? 0
    : skills.filter((s) => !selection.enabledSkills!.includes(s.slug)).length;

  const isToolEnabled = useCallback((id: string) =>
    selection.enabledTools === null || selection.enabledTools.includes(id),
  [selection.enabledTools]);

  const isSkillEnabled = useCallback((slug: string) =>
    selection.enabledSkills === null || selection.enabledSkills.includes(slug),
  [selection.enabledSkills]);

  const toolConfig: ToolConfig | undefined =
    (isAllToolsEnabled && isAllSkillsEnabled) ? undefined : {
      enabled_skills: selection.enabledSkills,
      enabled_tools: selection.enabledTools,
    };

  return {
    tools,
    skills,
    loadState,
    loadCatalog,
    toggleTool,
    toggleSkill,
    enableAllTools,
    enableAllSkills,
    isAllToolsEnabled,
    isAllSkillsEnabled,
    disabledToolsCount,
    disabledSkillsCount,
    isToolEnabled,
    isSkillEnabled,
    toolConfig,
    isAlwaysEnabled: (toolId: string) => ALWAYS_ENABLED_TOOLS.has(toolId),
    hasAgentScope: !!selectedAgent && (selectedAgent.tools.length > 0 || selectedAgent.skills.length > 0),
  };
}
