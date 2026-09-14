"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchAvailableTools,
  type AvailableTool,
  type AvailableSkill,
  type ToolConfig,
} from "@/lib/api";
import {
  isRequiredTool,
  REQUIRED_TOOLS,
  setItems,
  type PickerDomain,
} from "./picker-selection";

export type ToolsLoadState = "idle" | "loading" | "ready" | "error";
export interface ToolsSelection {
  enabledSkills: string[] | null;
  enabledTools: string[] | null;
}
const fromConfig = (config?: ToolConfig | null): ToolsSelection => ({
  enabledSkills: config?.enabled_skills ?? null,
  enabledTools:
    config?.enabled_tools == null
      ? null
      : [...new Set([...config.enabled_tools, ...REQUIRED_TOOLS])],
});

export function useToolsPicker(initial?: ToolConfig | null) {
  const [tools, setTools] = useState<AvailableTool[]>([]);
  const [skills, setSkills] = useState<AvailableSkill[]>([]);
  const [loadState, setLoadState] = useState<ToolsLoadState>("idle");
  const [selection, setSelection] = useState<ToolsSelection>(() =>
    fromConfig(initial),
  );
  const fetchedRef = useRef(false);
  const reset = useCallback(
    (config?: ToolConfig | null) => setSelection(fromConfig(config)),
    [],
  );
  // A conversation loaded asynchronously replaces the composer configuration.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reset(initial);
  }, [initial, reset]);

  const loadCatalog = useCallback(async () => {
    if (fetchedRef.current) return;
    fetchedRef.current = true;
    setLoadState("loading");
    try {
      const data = await fetchAvailableTools();
      setTools(data.tools);
      setSkills(data.skills);
      setLoadState("ready");
    } catch (e) {
      console.warn("Failed to load available tools", e);
      fetchedRef.current = false;
      setLoadState("error");
    }
  }, []);

  const setEnabled = useCallback(
    (domain: PickerDomain, ids: string[], enabled: boolean) => {
      const key = domain === "tools" ? "enabledTools" : "enabledSkills";
      const catalog =
        domain === "tools" ? tools.map((t) => t.id) : skills.map((s) => s.slug);
      setSelection((prev) => ({
        ...prev,
        [key]: setItems(
          prev[key],
          catalog,
          ids,
          enabled,
          domain === "tools" ? REQUIRED_TOOLS : [],
        ),
      }));
    },
    [tools, skills],
  );
  const setAll = useCallback((domain: PickerDomain, enabled: boolean) => {
    setSelection((prev) => ({
      ...prev,
      [domain === "tools" ? "enabledTools" : "enabledSkills"]: enabled
        ? null
        : domain === "tools"
          ? [...REQUIRED_TOOLS]
          : [],
    }));
  }, []);
  // Explicit nulls let a previously restricted conversation/task restore all mode.
  const toolConfig: ToolConfig = {
    enabled_skills: selection.enabledSkills,
    enabled_tools: selection.enabledTools,
  };
  return {
    tools,
    skills,
    selection,
    loadState,
    loadCatalog,
    setEnabled,
    setAll,
    reset,
    toolConfig,
    isAlwaysEnabled: isRequiredTool,
  };
}
