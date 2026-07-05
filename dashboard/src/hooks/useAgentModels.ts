"use client";

import { useEffect, useState } from "react";
import { fetchAgentModels, type AgentModel } from "@/lib/api";

const MODEL_STORAGE_KEY = "dashboard-chat-selected-model";

export const FAVORITE_MODEL_IDS = [
  "opencode-go/deepseek-v4-flash",
  "anthropic/claude-opus-4-8",
  "anthropic/claude-fable-5",
  "anthropic/claude-opus-4-7",
  "anthropic/claude-opus-4-6",
  "openai/gpt-5.5",
] as const;

export function favoriteModelRank(model: AgentModel): number | null {
  const index = FAVORITE_MODEL_IDS.indexOf(model.id as typeof FAVORITE_MODEL_IDS[number]);
  return index === -1 ? null : index;
}

export function isFavoriteModel(model: AgentModel): boolean {
  return favoriteModelRank(model) !== null;
}

export type ModelLoadState = "loading" | "ready" | "error";

/**
 * Agent-model catalog + selection, shared by the chat composer and the tasks
 * quick-add composer. Selection priority: explicit initialModel (e.g. a board
 * task's chosen model) > saved preference > backend default. Selecting a
 * model persists it as the saved preference.
 */
export function useAgentModels(initialModel?: string) {
  const [models, setModels] = useState<AgentModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [loadState, setLoadState] = useState<ModelLoadState>("loading");

  useEffect(() => {
    let cancelled = false;

    async function loadModels() {
      setLoadState("loading");
      try {
        const catalog = await fetchAgentModels();
        if (cancelled) return;
        const list = catalog.models || [];
        setModels(list);

        const saved = typeof window !== "undefined"
          ? window.localStorage.getItem(MODEL_STORAGE_KEY)
          : null;
        const savedIsValid = saved && list.some((model) => model.id === saved);
        const initialIsValid = initialModel && list.some((model) => model.id === initialModel);
        const nextModel = initialIsValid
          ? initialModel
          : savedIsValid
            ? saved
            : catalog.default_model || list[0]?.id || "";
        setSelectedModel(nextModel);
        setLoadState("ready");
      } catch (e) {
        if (cancelled) return;
        console.warn("Failed to load agent models", e);
        setModels([]);
        setSelectedModel("");
        setLoadState("error");
      }
    }

    loadModels();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialModel]);

  const selectModel = (value: string) => {
    setSelectedModel(value);
    try {
      window.localStorage.setItem(MODEL_STORAGE_KEY, value);
    } catch {}
  };

  return { models, selectedModel, selectModel, loadState };
}
